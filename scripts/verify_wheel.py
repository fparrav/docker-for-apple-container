#!/usr/bin/env python3
"""Install the built wheel into a clean virtual environment and exercise it.

A release is only as good as the files users install, so this script takes the
wheel the build produced, installs it into a new venv that contains nothing from
the checkout, and checks what a user would see: the ``docker`` entry point is in
the wheel, the package imports from ``site-packages``, ``docker version`` reports
the shim version of the tag, and the executable fails cleanly when no Apple
``container`` CLI is available. A stand-in ``container`` script answers the two
calls ``docker version`` makes, so no Apple runtime is needed.

Usage:
    verify_wheel.py --dist DIR [--expect-version VERSION]

Exit status:
    0  every check passed (one ``ok`` line each)
    2  usage error
    3  a check failed; one line on stderr names it

The output also carries an ``ok environment: Python <version> on <platform>``
line: the interpreter version and the platform that ran the checks, so a log
shows what was verified.

Only the standard library is used. The virtual environment is built in a
temporary directory and removed afterwards, and the subprocesses run with an
environment built from scratch, so variables such as
``CONTAINER_DOCKER_SHIM_CONTAINER`` or ``PYTHONPATH`` cannot leak in.
"""
from __future__ import annotations

import argparse
import configparser
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path

PACKAGE = "container_docker_shim"
ENTRY_POINT = ("docker", f"{PACKAGE}.cli:main")
RUNTIME_ABSENT_MESSAGE = "Apple container CLI not found"
SOURCE_VERSION = "0+source"
SUBPROCESS_TIMEOUT = 120

# The two calls ``docker version`` makes: ``container --version`` and
# ``container system status``. Anything else exits 0 silently.
STAND_IN = """#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "container CLI version 1.5.0 (build: release, commit: unspeci)"
fi
exit 0
"""


class CheckError(Exception):
    """A check failed; the message says which and why."""


def find_wheel(dist: Path) -> Path:
    """The single wheel in ``dist``, resolved to an absolute path."""
    wheels = sorted(dist.glob("*.whl")) if dist.is_dir() else []
    if not wheels:
        raise CheckError(f"no wheel found in {dist}")
    if len(wheels) > 1:
        names = ", ".join(path.name for path in wheels)
        raise CheckError(f"expected one wheel in {dist}, found {len(wheels)}: {names}")
    return wheels[0].resolve()


def check_contents(wheel: Path) -> None:
    """The package files and the ``docker`` console script are in the wheel."""
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
            entry_files = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
            entry_text = archive.read(entry_files[0]).decode("utf-8") if entry_files else ""
    except (OSError, zipfile.BadZipFile) as exc:
        raise CheckError(f"{wheel.name} is not a readable wheel: {exc}")
    for required in (f"{PACKAGE}/__init__.py", f"{PACKAGE}/cli.py"):
        if required not in names:
            raise CheckError(f"{wheel.name} lacks {required}")
    if not entry_files:
        raise CheckError(f"{wheel.name} has no entry_points.txt")
    parser = configparser.ConfigParser()
    parser.read_string(entry_text)
    declared = parser.get("console_scripts", ENTRY_POINT[0], fallback=None)
    if declared is None or declared.strip() != ENTRY_POINT[1]:
        raise CheckError(
            f"{wheel.name} does not declare the console script "
            f"{ENTRY_POINT[0]} = {ENTRY_POINT[1]} (found {declared!r})"
        )


def clean_env(path: str, home: Path) -> dict:
    """An environment built from scratch: only what a fresh shell would have."""
    return {"PATH": path, "HOME": str(home), "LANG": "C.UTF-8"}


def run(argv: list, *, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckError(f"could not run {' '.join(str(a) for a in argv)}: {exc}")


def venv_bin(env_dir: Path) -> Path:
    return env_dir / ("Scripts" if os.name == "nt" else "bin")


def install(wheel: Path, env_dir: Path, work: Path) -> None:
    """Create the venv and install the wheel without touching any index."""
    venv.EnvBuilder(with_pip=True, clear=True).create(str(env_dir))
    python = venv_bin(env_dir) / "python"
    result = run(
        [
            str(python), "-m", "pip", "install",
            "--disable-pip-version-check", "--no-index", "--no-deps", str(wheel),
        ],
        cwd=work,
        env=clean_env(str(venv_bin(env_dir)), work),
    )
    if result.returncode != 0:
        raise CheckError(f"pip could not install {wheel}: {(result.stderr or result.stdout).strip()[-400:]}")


def check_import_location(env_dir: Path, work: Path) -> None:
    """The package comes from the environment, not from a checkout."""
    python = venv_bin(env_dir) / "python"
    result = run(
        [str(python), "-I", "-c", f"import {PACKAGE}; print({PACKAGE}.__file__)"],
        cwd=work,
        env=clean_env(str(venv_bin(env_dir)), work),
    )
    if result.returncode != 0:
        raise CheckError(f"the installed package does not import: {(result.stderr or result.stdout).strip()[-400:]}")
    location = Path(result.stdout.strip()).resolve()
    if env_dir.resolve() not in location.parents or "site-packages" not in location.parts:
        raise CheckError(f"{PACKAGE} was imported from {location}, not from the environment's site-packages")


def write_stand_in(directory: Path) -> Path:
    path = directory / "container"
    path.write_text(STAND_IN, encoding="utf-8")
    path.chmod(0o755)
    return path


def check_version(env_dir: Path, work: Path, expected: str | None) -> str:
    """``docker version`` runs against the stand-in and names the shim version."""
    stand_in = write_stand_in(work)
    env = clean_env(str(venv_bin(env_dir)), work)
    env["CONTAINER_DOCKER_SHIM_CONTAINER"] = str(stand_in)
    result = run([str(venv_bin(env_dir) / "docker"), "version"], cwd=work, env=env)
    if result.returncode != 0:
        raise CheckError(
            f"`docker version` exited {result.returncode}: {(result.stderr or result.stdout).strip()[-400:]}"
        )
    shim = None
    for line in result.stdout.splitlines():
        if line.startswith("Shim:"):
            shim = line.split(":", 1)[1].strip()
    if not shim:
        raise CheckError("`docker version` printed no `Shim:` line")
    if expected is not None and shim != expected:
        raise CheckError(f"`docker version` reports shim {shim}, expected {expected}")
    if expected is None and shim == SOURCE_VERSION:
        raise CheckError(f"`docker version` reports {SOURCE_VERSION}: the wheel carries no version metadata")
    return shim


def check_runtime_absent(env_dir: Path, work: Path) -> None:
    """With no Apple ``container`` CLI the executable exits 1 and says so."""
    empty = work / "empty-path"
    empty.mkdir(exist_ok=True)
    env = clean_env(str(empty), work)
    result = run([str(venv_bin(env_dir) / "docker"), "ps"], cwd=work, env=env)
    if result.returncode != 1:
        raise CheckError(f"`docker ps` without the runtime exited {result.returncode}, expected 1")
    if RUNTIME_ABSENT_MESSAGE not in (result.stderr + result.stdout):
        raise CheckError(f"`docker ps` without the runtime did not say {RUNTIME_ABSENT_MESSAGE!r}")


def verify(dist: Path, expected_version: str | None) -> list:
    """Run every check; return the ``ok`` lines. Raises ``CheckError`` on the first failure."""
    lines = []
    wheel = find_wheel(dist)
    lines.append(f"ok wheel {wheel.name}")
    check_contents(wheel)
    lines.append("ok contents: package files and the docker console script")
    lines.append(f"ok environment: Python {platform.python_version()} on {platform.platform()}")
    work = Path(tempfile.mkdtemp(prefix="verify-wheel-"))
    try:
        env_dir = work / "venv"
        install(wheel, env_dir, work)
        lines.append("ok install in a clean virtual environment")
        check_import_location(env_dir, work)
        lines.append("ok import location: site-packages of the environment")
        shim = check_version(env_dir, work, expected_version)
        lines.append(f"ok docker version: shim {shim}")
        check_runtime_absent(env_dir, work)
        lines.append("ok runtime absent: exit 1 with a clear message")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return lines


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dist", required=True, help="directory holding the built wheel")
    parser.add_argument("--expect-version", default=None, help="version the wheel must report, for example 0.4.0rc1")
    args = parser.parse_args(argv)
    try:
        for line in verify(Path(args.dist), args.expect_version):
            print(line)
    except CheckError as exc:
        print(f"wheel check: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
