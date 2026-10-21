"""Compose normalizer adapter around the official `docker-compose` binary.

The adapter runs ``docker-compose <global flags> config --format json`` and
converts the JSON into the model that ``compose.py`` reads, so compose
semantics (merging, interpolation, profiles) stay Compose's.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

# When set, names the exact `docker-compose` executable to use (never
# searched on `PATH`); the value must be an existing file.
EXECUTABLE_ENV = "CONTAINER_DOCKER_SHIM_COMPOSE"
# Set in the child's environment so a re-entering call (for example through
# a `docker compose` plugin wrapper that re-invokes the shim) can be
# detected and refused.
MARKER_ENV = "CONTAINER_DOCKER_SHIM_NORMALIZING"
# Top-level keys of the normalized output that ``convert_model`` drops on
# purpose. ``x-*`` extension keys are dropped too (their names are not
# fixed), and secrets no service build references are dropped from the
# preserved ``secrets`` key (the official binary already prunes the ones
# nothing uses).
DROPPED_TOP_LEVEL = frozenset({"configs"})


class NormalizerError(Exception):
    """A normalizer failure; ``.code`` is the shim's exit code."""

    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Inputs:
    """Compose global flags to forward to the normalizer call.

    ``files``, ``env_files`` and ``profiles`` are tuples of strings, one
    entry per repeated ``-f``, ``--env-file`` or ``--profile`` flag;
    ``project_name`` and ``project_dir`` map to ``-p`` and
    ``--project-directory``.
    """

    files: tuple = ()
    env_files: tuple = ()
    profiles: tuple = ()
    project_name: Optional[str] = None
    project_dir: Optional[str] = None


def build_argv(executable: str, inputs: Inputs, tail: Sequence[str]) -> list:
    """Build the full argv for one normalizer call.

    The order is fixed: the executable, every ``-f`` file, every
    ``--env-file`` env file, every ``--profile`` profile, ``-p`` project
    name, ``--project-directory``, and then ``tail`` (the subcommand, e.g.
    ``["config", "--format", "json"]``). ``None`` or empty-string entries
    are omitted, so ``build_argv("docker-compose", Inputs(), tail)``
    returns ``["docker-compose", *tail]``.
    """
    argv = [executable]
    for flag in inputs.files:
        if flag:
            argv += ["-f", flag]
    for env_file in inputs.env_files:
        if env_file:
            argv += ["--env-file", env_file]
    for profile in inputs.profiles:
        if profile:
            argv += ["--profile", profile]
    if inputs.project_name:
        argv += ["-p", inputs.project_name]
    if inputs.project_dir:
        argv += ["--project-directory", inputs.project_dir]
    argv.extend(tail)
    return argv


def resolve_executable(
    env: Mapping[str, Optional[str]],
    which: Callable[[str], Optional[str]],
    launcher_paths: Sequence[str],
    is_file=os.path.isfile,
    realpath=os.path.realpath,
) -> Optional[str]:
    """Pick the ``docker-compose`` executable for a normalizer call.

    When ``env`` has ``EXECUTABLE_ENV`` set to a non-empty value, that
    value is used as-is and never searched on ``PATH``; a value that is
    not an existing file raises ``NormalizerError`` naming the variable
    and the path. Otherwise the candidate is ``which("docker-compose")``,
    or ``None`` when the binary is not on ``PATH``.

    The loop guard refuses re-entry before returning a candidate: when
    ``env`` already has ``MARKER_ENV`` set, ``NormalizerError`` says the
    shim was ``re-entered``; and when ``realpath`` of the candidate
    equals the ``realpath`` of any ``launcher_paths`` entry (checked for
    the explicit value and the ``PATH`` candidate alike),
    ``NormalizerError`` says a ``loop`` was detected and names the
    offending path. With no candidate at all, ``None`` is returned even
    when the marker is set.
    """
    explicit = env.get(EXECUTABLE_ENV)
    if explicit:
        if not is_file(explicit):
            raise NormalizerError(
                "%s names a path that is not an existing file: %s"
                % (EXECUTABLE_ENV, explicit)
            )
        candidate = explicit
    else:
        candidate = which("docker-compose")
    if not candidate:
        return None
    if MARKER_ENV in env:
        raise NormalizerError(
            "%s is already set: the shim re-entered through %s; refusing "
            "to run the compose normalizer a second time" % (MARKER_ENV, candidate)
        )
    for launcher in launcher_paths:
        if realpath(candidate) == realpath(launcher):
            raise NormalizerError(
                "executable %s resolves to shim launcher path %s: detected "
                "a loop, refusing to run it" % (candidate, launcher)
            )
    return candidate


def child_env(env: Mapping[str, Optional[str]]) -> dict:
    """Environment for the compose normalizer child process.

    A copy of ``env`` with ``MARKER_ENV`` set to ``"1"`` so a wrapper
    around the normalizer that re-invokes the shim is caught by the loop
    guard in :func:`resolve_executable`. The argument is not mutated.
    """
    child = dict(env)
    child[MARKER_ENV] = "1"
    return child


def load_normalized(
    executable: str,
    inputs: Inputs,
    run: Callable[[list, dict], Any],
    env: Mapping[str, Optional[str]],
) -> tuple[dict, str]:
    """Fetch the normalized compose model and return it with the stderr.

    Runs ``run(build_argv(executable, inputs, ["config", "--format",
    "json"]), child_env(env))``; ``run`` returns an object with
    ``returncode``, ``stdout`` and ``stderr`` (strings). On success the
    parsed JSON object is returned together with the child's ``stderr``
    text. A non-zero exit code raises ``NormalizerError`` (code 1)
    carrying the stripped ``stderr``, or
    ``"docker-compose config failed"`` when that is empty; ``stdout``
    that is not valid JSON, or whose top-level value is not a JSON
    object, raises ``NormalizerError`` naming the problem; an
    ``OSError`` from ``run`` is reported with its text. No process is
    spawned here; the caller owns execution.
    """
    argv = build_argv(executable, inputs, ["config", "--format", "json"])
    try:
        result = run(argv, child_env(env))
    except OSError as exc:
        raise NormalizerError(str(exc))
    if result.returncode != 0:
        message = str(result.stderr).strip()
        raise NormalizerError(
            message if message else "docker-compose config failed",
            code=1,
        )
    try:
        model = json.loads(str(result.stdout))
    except ValueError as exc:
        raise NormalizerError(
            "compose normalizer returned invalid JSON: %s" % exc
        )
    if not isinstance(model, dict):
        raise NormalizerError(
            "compose normalizer returned a JSON %s, expected an object"
            % type(model).__name__
        )
    return model, str(result.stderr)


def executable_version(
    executable: str,
    run: Callable[[list, dict], Any],
    env: Mapping[str, Optional[str]],
) -> str:
    """Return the executable's short version string, or ``"unknown"``.

    Runs ``run([executable, "version", "--short"], child_env(env))``
    (``run`` returns an object with ``returncode``, ``stdout`` and
    ``stderr``, as in :func:`load_normalized`) and returns the stripped
    ``stdout``. A non-zero exit code, a ``stdout`` that is blank once
    stripped, or an ``OSError`` from ``run`` all yield ``"unknown"``;
    this is a best-effort query that never raises. No process is
    spawned here; the caller owns execution.
    """
    try:
        result = run([executable, "version", "--short"], child_env(env))
    except OSError:
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    version = str(result.stdout).strip()
    return version if version else "unknown"


def _convert_port(port: Any) -> Any:
    """Render one normalized ``ports`` object as a compose port string.

    The result is ``[host_ip:]published:target`` when ``published`` is
    present, else just ``target``; ``/<protocol>`` is appended only when
    the protocol is not ``tcp`` (a missing ``host_ip`` is simply omitted).
    Entries that are not mappings are copied unchanged.
    """
    if not isinstance(port, dict):
        return port
    published = port.get("published")
    host_ip = port.get("host_ip")
    protocol = port.get("protocol") or "tcp"
    if published:
        result = "%s:%s" % (published, port.get("target"))
        if host_ip:
            result = "%s:%s" % (host_ip, result)
    else:
        result = str(port.get("target"))
    if protocol != "tcp":
        result += "/%s" % protocol
    return result


def _unescape_dollars(value: Any) -> Any:
    """Undo the ``$`` -> ``$$`` escaping ``docker-compose config`` applies.

    The normalized output is meant to be fed back to Compose, so every
    literal ``$`` comes out as ``$$``. The shim consumes the model directly
    and never re-interpolates it, so the escape has to be removed here;
    otherwise ``command: echo $$HOME`` would reach the container verbatim.
    Strings, list items and mapping keys and values are converted
    recursively; every other value is returned unchanged.
    """
    if isinstance(value, str):
        return value.replace("$$", "$")
    if isinstance(value, list):
        return [_unescape_dollars(item) for item in value]
    if isinstance(value, dict):
        return {
            _unescape_dollars(key): _unescape_dollars(item)
            for key, item in value.items()
        }
    return value


def _convert_service(service: Mapping[str, Any]) -> dict:
    """Copy a service, dropping top-level ``None`` keys and rendering ports.

    Only keys whose value is ``None`` are dropped (no recursion: e.g.
    ``networks: {"default": None}`` is kept as-is); its ``ports`` entries
    are rendered to strings by :func:`_convert_port`; every other value is
    copied unchanged.
    """
    result: dict = {}
    for key, value in service.items():
        if value is None:
            continue
        if key == "ports" and isinstance(value, list):
            value = [_convert_port(entry) for entry in value]
        result[key] = value
    return result


def _build_secrets(
    services: Mapping[str, Any], definitions: Mapping[str, Any]
) -> dict:
    """Return the secret definitions every service build references.

    For every service whose ``build`` is a mapping, each ``secrets``
    entry (a string, or a mapping with a ``source``) names a definition
    to copy from ``definitions`` whole; a referenced name that is missing
    there is simply not copied (the resolver reports it later). A build
    that is a plain string, or has no ``secrets``, contributes nothing.
    """
    kept: dict = {}
    for service in services.values():
        build = service.get("build")
        if not isinstance(build, dict):
            continue
        for entry in build.get("secrets") or []:
            if isinstance(entry, str):
                source = entry
            elif isinstance(entry, dict):
                source = entry.get("source")
            else:
                source = None
            if isinstance(source, str) and source in definitions:
                kept[source] = definitions[source]
    return kept


def convert_model(raw: Any, version: str) -> dict:
    """Convert the normalized JSON into the model dictionary ``compose.py`` reads.

    Returns ``{"name", "services", "networks", "volumes", "secrets"}``
    taken from ``raw`` (missing ``networks``/``volumes``/``secrets``
    become ``{}``); ``secrets`` keeps only the definitions referenced by a
    service ``build.secrets`` entry, whole and un-escaped (see
    :func:`_build_secrets`); inside every service, keys whose value is
    ``None`` are dropped (no recursion) and each ``ports`` object becomes a
    port string (see :func:`_convert_port`); the ``$$`` escape the
    normalizer adds is undone in every service string and in the preserved
    ``networks``, ``volumes`` and ``secrets`` sections (see
    :func:`_unescape_dollars`); every other service value is copied
    unchanged. Top-level ``x-*`` keys, ``configs`` (see
    :data:`DROPPED_TOP_LEVEL`) and everything else are ignored.

    A ``raw`` that is not a mapping, has no ``name`` string, has
    ``services`` that is not a mapping, has a service that is not a
    mapping, or has ``secrets`` that is not a mapping raises
    ``NormalizerError`` whose message names ``Docker Compose <version>``
    and the offending key.
    """
    if not isinstance(raw, dict):
        raise NormalizerError(
            "Docker Compose %s: the normalized model must be a mapping, "
            "got %s" % (version, type(raw).__name__)
        )
    name = raw.get("name")
    if not isinstance(name, str):
        raise NormalizerError(
            "Docker Compose %s: 'name' must be a string, got %r"
            % (version, name)
        )
    services = raw.get("services")
    if services is None:
        services = {}
    if not isinstance(services, dict):
        raise NormalizerError(
            "Docker Compose %s: 'services' must be a mapping, got %s"
            % (version, type(services).__name__)
        )
    secrets = raw.get("secrets")
    if secrets is None:
        secrets = {}
    if not isinstance(secrets, dict):
        raise NormalizerError(
            "Docker Compose %s: 'secrets' must be a mapping, got %s"
            % (version, type(secrets).__name__)
        )
    secrets = _unescape_dollars(secrets)
    converted: dict = {}
    for service_name, service in services.items():
        if not isinstance(service, dict):
            raise NormalizerError(
                "Docker Compose %s: service %r must be a mapping, got %s"
                % (version, service_name, type(service).__name__)
            )
        converted[service_name] = _unescape_dollars(_convert_service(service))
    return {
        "name": name,
        "services": converted,
        "networks": _unescape_dollars(raw.get("networks") or {}),
        "volumes": _unescape_dollars(raw.get("volumes") or {}),
        "secrets": _build_secrets(converted, secrets),
    }
