#!/usr/bin/env python3
"""Validate a candidate Apple ``container`` release against the compatibility matrix.

Read-only matrix check
    For every matrix entry that names an Apple command or flag, ask the
    candidate runtime's own ``--help`` whether it still exists. Nothing is run
    or created.

Live tier
    Runs ``tests/test_live_*.py`` with ``SHIM_TIERS=live`` (disposable
    ``shimtest-`` objects only, never a prune) and maps every test result back
    to the matrix entries that cite it.

The output is a per-entry evidence table plus a summary. A tier that did not
run is reported as "not run", never as passed. The script never edits the
supported-version constant: advancing it stays a deliberate, separate change.

Usage:
    scripts/validate-runtime.py [--container PATH] [--skip-live] [--output FILE]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import compat_matrix  # noqa: E402
from container_docker_shim.cli import SUPPORTED_CONTAINER_VERSION  # noqa: E402

HELP_TIMEOUT = 20
LIVE_TIMEOUT = 3600

# Matrix id prefix -> the Apple command whose --help documents its flags.
OPTION_COMMANDS = {"run": ["run"], "build": ["build"]}


def _help(container: str, words: list, cache: dict) -> tuple:
    key = tuple(words)
    if key not in cache:
        try:
            result = subprocess.run(
                [container, *words, "--help"],
                capture_output=True,
                text=True,
                timeout=HELP_TIMEOUT,
            )
            cache[key] = (result.returncode, result.stdout + result.stderr)
        except (OSError, subprocess.TimeoutExpired) as exc:
            cache[key] = (127, str(exc))
    return cache[key]


def _flag_command(entry: dict) -> list:
    """The Apple command whose ``--help`` should document this entry's flag."""
    prefix = entry["id"].split()[0]
    if prefix == "compose":
        return ["build"] if entry["id"].startswith("compose build.") else ["run"]
    return OPTION_COMMANDS.get(prefix) or []


def check_entry(entry: dict, container: str, cache: dict) -> str:
    """Return ``held``, ``missing`` or ``not checked`` for the Apple side of an entry."""
    apple = entry.get("apple")
    if not apple:
        return "not checked"
    if not apple.startswith("-"):
        # A command (or sub-command) name: its own --help must exit 0.
        words = apple.split()
        if words and words[0] == "container":
            words = words[1:]
        code, _ = _help(container, words, cache)
        return "held" if code == 0 else "missing"
    command = _flag_command(entry)
    if not command:
        return "not checked"
    code, text = _help(container, command, cache)
    if code != 0:
        return "missing"
    flag = apple.split()[0]  # `--ssh default` documents the flag `--ssh`
    return "held" if re.search(r"(?<![\w-])" + re.escape(flag) + r"(?![\w-])", text) else "missing"


def run_live(container: str) -> tuple:
    """Run the live tier; return (ran, {test id: outcome}, raw output)."""
    env = dict(os.environ, SHIM_TIERS="live", CONTAINER_DOCKER_SHIM_CONTAINER=container)
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-p", "test_live_*.py", "-v"],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=LIVE_TIMEOUT,
    )
    outcomes = {}
    # `unittest -v` prints `method (module.Class.method) ... ok` on 3.11+ and
    # `method (module.Class) ... ok` before; accept both.
    pattern = re.compile(r"^(\w+) \(([\w.]+)\).* \.\.\. (ok|FAIL|ERROR|skipped.*)$")
    for line in (result.stdout + result.stderr).splitlines():
        match = pattern.match(line)
        if not match:
            continue
        method, dotted, outcome = match.groups()
        test_id = dotted if dotted.endswith("." + method) else f"{dotted}.{method}"
        outcomes[test_id] = (
            "passed" if outcome == "ok" else ("not run" if outcome.startswith("skipped") else "FAILED")
        )
    return bool(outcomes) and any(v != "not run" for v in outcomes.values()), outcomes, result.stdout + result.stderr


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--container", default=os.environ.get("CONTAINER_DOCKER_SHIM_CONTAINER") or "container")
    parser.add_argument("--skip-live", action="store_true", help="only run the read-only matrix check")
    parser.add_argument("--output", help="also write the summary to this file")
    args = parser.parse_args(argv)

    try:
        version = subprocess.run(
            [args.container, "--version"], capture_output=True, text=True, timeout=HELP_TIMEOUT
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"cannot run {args.container}: {exc}", file=sys.stderr)
        return 2
    lines = [
        f"candidate runtime : {version or 'unavailable'}",
        f"supported version : {SUPPORTED_CONTAINER_VERSION} (this script never changes it)",
    ]

    entries = compat_matrix.load()
    errors = compat_matrix.validate(entries)
    if errors:
        print("\n".join(["matrix is invalid:"] + errors), file=sys.stderr)
        return 2

    cache: dict = {}
    apple = {entry["id"]: check_entry(entry, args.container, cache) for entry in entries}

    live_ran, outcomes = False, {}
    if args.skip_live:
        lines.append("live tier         : not run (--skip-live)")
    else:
        live_ran, outcomes, _ = run_live(args.container)
        lines.append("live tier         : " + ("ran" if live_ran else "not run (skipped or runtime unavailable)"))

    rows = ["", "| id | claimed | apple side | live test |", "| --- | --- | --- | --- |"]
    problems = 0
    for entry in entries:
        claimed = entry["evidence"]
        test = entry.get("test")
        live = "-"
        if test and claimed == "live":
            live = outcomes.get(test, "not run") if live_ran else "not run"
            if live == "FAILED":
                problems += 1
        if apple[entry["id"]] == "missing":
            problems += 1
        rows.append(f"| {entry['id']} | {claimed} | {apple[entry['id']]} | {live} |")

    lines += rows
    lines += ["", f"entries: {len(entries)}; problems: {problems}"]
    text = "\n".join(lines)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
