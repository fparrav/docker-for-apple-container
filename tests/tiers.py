"""Shared tier gating and disposable-name helpers for the test suite.

The suite runs in four tiers (see ``docs/testing.md``): ``unit`` and ``fake``
always run; ``live`` and ``conformance`` are opt-in through ``SHIM_TIERS``
(comma-separated). A tier that is enabled but cannot run (no ``container``
runtime, a release other than the supported one, no Engine client) is
reported as skipped with the reason, never as passed.
"""

from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from typing import Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim.cli import SUPPORTED_CONTAINER_VERSION  # noqa: E402

TIERS_ENV = "SHIM_TIERS"
CONFORMANCE_DOCKER_ENV = "SHIM_CONFORMANCE_DOCKER"
CONTAINER_BIN_ENV = "CONTAINER_DOCKER_SHIM_CONTAINER"
OPT_IN_TIERS = ("live", "conformance")
NAME_PREFIX = "shimtest-"
RUN_TIMEOUT_SECONDS = 120

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def enabled_tiers(env: Mapping[str, str] | None = None) -> frozenset[str]:
    """Parse ``SHIM_TIERS`` (comma-separated) into a set of tiers.

    An absent or empty variable enables no opt-in tier. ``env`` defaults to
    the process environment so tests can inject one.
    """
    if env is None:
        env = os.environ
    raw = env.get(TIERS_ENV, "")
    return frozenset(tier.strip().lower() for tier in raw.split(",") if tier.strip())


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """Run ``args`` (a full argv starting with ``container``) with captured output.

    Honours ``CONTAINER_DOCKER_SHIM_CONTAINER`` so the live tier works when
    ``container`` is not on ``PATH`` (the same override the shim itself uses).
    """
    container = os.environ.get(CONTAINER_BIN_ENV) or args[0]
    return subprocess.run(
        [container, *args[1:]],
        capture_output=True,
        text=True,
        check=False,
        timeout=RUN_TIMEOUT_SECONDS,
    )


def _reported_version(output: str) -> str | None:
    """Read the ``server.version`` (or ``client.version``) line of the status output."""
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("server.version", "client.version"):
            return parts[1]
    return None


def runtime_skip_reason(run: Runner | None = None) -> str | None:
    """Return ``None`` when the Apple runtime is usable, else the reason it is not.

    The reason is a human-readable string for skip messages: the runtime
    binary is missing, ``container system status`` fails, or the reported
    version differs from ``cli.SUPPORTED_CONTAINER_VERSION``.
    """
    runner = run if run is not None else _run
    try:
        proc = runner(["container", "system", "status"])
    except (OSError, subprocess.SubprocessError) as exc:
        return f"container runtime not available: {exc}"
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        return f"container system status exited {proc.returncode}: {output}"
    version = _reported_version(proc.stdout or "")
    if version is None:
        return f"container system status reports no version: {output}"
    if version != SUPPORTED_CONTAINER_VERSION:
        return (
            f"container {version} differs from the supported "
            f"version {SUPPORTED_CONTAINER_VERSION}"
        )
    return None


def live_tier(run: Runner | None = None):
    """Decorator that runs a test only when the live tier is enabled and usable.

    Skips with the reason otherwise: the tier is not in ``SHIM_TIERS`` or
    the runtime check in ``runtime_skip_reason`` fails.
    """

    def decorator(func: Callable[..., object]):
        @functools.wraps(func)
        def wrapper(*args: object, **kwargs: object) -> object:
            if "live" not in enabled_tiers():
                raise unittest.SkipTest(
                    f"live tier disabled: set {TIERS_ENV}=live to run live tests"
                )
            reason = runtime_skip_reason(run)
            if reason is not None:
                raise unittest.SkipTest(f"live tier cannot run: {reason}")
            return func(*args, **kwargs)

        return wrapper

    return decorator


def conformance_tier(run: Runner | None = None):
    """Decorator that runs a test only when the conformance tier can run.

    Requires ``SHIM_TIERS`` to contain ``conformance``,
    ``SHIM_CONFORMANCE_DOCKER`` to point at a real Docker Engine client
    (a bare ``docker`` on this machine would be the shim itself), and the
    same usable runtime the live tier needs: the shim side of a comparison
    still runs on the supported Apple ``container`` release.
    """

    def decorator(func: Callable[..., object]):
        @functools.wraps(func)
        def wrapper(*args: object, **kwargs: object) -> object:
            if "conformance" not in enabled_tiers():
                raise unittest.SkipTest(
                    f"conformance tier disabled: set {TIERS_ENV}=conformance "
                    f"and {CONFORMANCE_DOCKER_ENV} to run conformance tests"
                )
            if not os.environ.get(CONFORMANCE_DOCKER_ENV):
                raise unittest.SkipTest(
                    f"conformance tier cannot run: {CONFORMANCE_DOCKER_ENV} "
                    "is not set"
                )
            reason = runtime_skip_reason(run)
            if reason is not None:
                raise unittest.SkipTest(f"conformance tier cannot run: {reason}")
            return func(*args, **kwargs)

        return wrapper

    return decorator


def disposable_name(topic: str) -> str:
    """Name for a disposable live object: ``shimtest-<topic>-<8 hex>``.

    The ``shimtest-`` prefix lets cleanup address exactly these objects by
    name; tests must remove them in ``addCleanup`` and never prune.
    """
    return f"{NAME_PREFIX}{topic}-{uuid.uuid4().hex[:8]}"


def cleanup_by_prefix(prefix: str, run: Runner) -> None:
    """Stop and remove the live containers, volumes and networks owned by ``prefix``.

    Lists each kind through ``run`` (``container ls --all --format json``,
    ``container volume list --format json``, ``container network list --format
    json``) and removes only the objects whose name starts with the exact
    ``prefix``: containers are stopped before removal, volumes and networks
    are removed directly. A prefix that does not start with ``NAME_PREFIX``
    (``shimtest-``) is refused, a prune is never issued, and no object that
    does not carry the prefix is touched.
    """
    if not prefix.startswith(NAME_PREFIX):
        raise ValueError(
            f"refusing to clean objects outside {NAME_PREFIX!r}: {prefix!r}"
        )
    _remove_matching(
        prefix,
        ["container", "ls", "--all", "--format", "json"],
        run,
        _remove_container,
    )
    _remove_matching(
        prefix,
        ["container", "volume", "list", "--format", "json"],
        run,
        _remove_volume,
    )
    _remove_matching(
        prefix,
        ["container", "network", "list", "--format", "json"],
        run,
        _remove_network,
    )


def _object_name(entry: object) -> str | None:
    """Name of one listed object, or ``None`` when its shape is unrecognised.

    The runtime reports containers under ``id`` (and ``configuration.id``),
    volumes under ``name``, and networks under ``id`` (and
    ``configuration.name``); checking each of those locations in order
    covers every shape.
    """
    if not isinstance(entry, dict):
        return None
    for candidate in (entry, entry.get("configuration")):
        if not isinstance(candidate, dict):
            continue
        for key in ("name", "id"):
            value = candidate.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _proc_output(proc: subprocess.CompletedProcess) -> str:
    return ((proc.stdout or "") + (proc.stderr or "")).strip()


def _remove_matching(
    prefix: str, list_args: list[str], run: Runner, remove: Callable[[str, Runner], None]
) -> None:
    """List one kind of object through ``run`` and remove the prefixed ones."""
    proc = run(list_args)
    label = f"container {' '.join(list_args[1:])}"
    if proc.returncode != 0:
        raise RuntimeError(f"{label} exited {proc.returncode}: {_proc_output(proc)}")
    try:
        entries = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} returned invalid JSON: {exc}") from exc
    if not isinstance(entries, list):
        raise RuntimeError(f"{label} did not return a JSON array")
    for entry in entries:
        name = _object_name(entry)
        if name is not None and name.startswith(prefix):
            remove(name, run)


def _remove_container(name: str, run: Runner) -> None:
    # Stopping an already-stopped container can fail; what matters is the
    # removal, which is checked below.
    run(["container", "stop", name])
    remove = run(["container", "rm", name])
    if remove.returncode != 0:
        raise RuntimeError(
            f"container rm {name} exited {remove.returncode}: {_proc_output(remove)}"
        )


def _remove_volume(name: str, run: Runner) -> None:
    remove = run(["container", "volume", "rm", name])
    if remove.returncode != 0:
        raise RuntimeError(
            f"container volume rm {name} exited {remove.returncode}: {_proc_output(remove)}"
        )


def _remove_network(name: str, run: Runner) -> None:
    remove = run(["container", "network", "rm", name])
    if remove.returncode != 0:
        raise RuntimeError(
            f"container network rm {name} exited {remove.returncode}: {_proc_output(remove)}"
        )
