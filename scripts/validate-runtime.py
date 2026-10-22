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

Release evidence
    ``--evidence`` also writes ``docs/evidence/runtime-<version>-<UTC>.json``
    when the validation fully succeeded: every live test the sources declare
    ran and passed, on the supported ``container`` release, from a clean tree.
    The file records the runtime version, a hash of the git trees of ``src/``,
    ``scripts/`` and ``tests/`` at ``HEAD``, the UTC time and the live counts;
    ``scripts/verify_release_evidence.py`` checks it at release time. Nothing is
    written for a failed, partial or ``--skip-live`` run.

Usage:
    scripts/validate-runtime.py [--container PATH] [--skip-live] [--output FILE]
                                [--evidence [--evidence-dir DIR]]

Exit status:
    0  every requested check ran and held (or --skip-live with no problem)
    1  a check failed (a missing Apple flag, a failed live test, a runner error)
    2  usage error or invalid matrix
    3  incomplete: the live tier was requested but no test ran, or a live
       entry's test did not run, or (with --evidence) the live tier did not run
       every test the sources declare

When 1 and 3 both apply, 1 wins. The summary always ends with a ``result:``
line stating which case applies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

import compat_matrix  # noqa: E402
import test_report  # noqa: E402
from container_docker_shim.cli import SUPPORTED_CONTAINER_VERSION  # noqa: E402

HELP_TIMEOUT = 20
LIVE_TIMEOUT = 3600

EVIDENCE_DIR = ROOT / "docs" / "evidence"
EVIDENCE_SCHEMA = 1
# The directories whose git trees bind the evidence to the code it tested.
EVIDENCE_TREES = ("src", "scripts", "tests")

# Matrix id prefix -> the Apple command whose --help documents its flags.
OPTION_COMMANDS = {"run": ["run"], "build": ["build"]}


class Runtime:
    """The candidate ``container`` binary, probed read-only.

    Only ``--version`` and ``--help`` are issued; nothing is run or created.
    ``main`` accepts a substitute with the same two methods, which is how the
    unit tests exercise the script without a runtime.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._cache: dict = {}

    def version(self) -> str:
        result = subprocess.run(
            [self.path, "--version"], capture_output=True, text=True, timeout=HELP_TIMEOUT
        )
        return result.stdout.strip()

    def help(self, words: list) -> tuple:
        """(exit code, combined output) of ``<words> --help``, cached per command."""
        key = tuple(words)
        if key not in self._cache:
            try:
                result = subprocess.run(
                    [self.path, *words, "--help"],
                    capture_output=True,
                    text=True,
                    timeout=HELP_TIMEOUT,
                )
                self._cache[key] = (result.returncode, result.stdout + result.stderr)
            except (OSError, subprocess.TimeoutExpired) as exc:
                self._cache[key] = (127, str(exc))
        return self._cache[key]


def _flag_command(entry: dict) -> list:
    """The Apple command whose ``--help`` should document this entry's flag."""
    prefix = entry["id"].split()[0]
    if prefix == "compose":
        return ["build"] if entry["id"].startswith("compose build.") else ["run"]
    return OPTION_COMMANDS.get(prefix) or []


def _lists_flag(help_text: str, flag: str) -> bool:
    return bool(re.search(r"(?<![\w-])" + re.escape(flag) + r"(?![\w-])", help_text))


def check_entry(entry: dict, runtime: "Runtime") -> str:
    """Return ``held``, ``missing`` or ``not checked`` for the Apple side of an entry."""
    apple = entry.get("apple")
    if not apple:
        return "not checked"
    if not apple.startswith("-"):
        # One or more commands joined by ";" (for example `container --version;
        # container system status`): each part must hold.
        for part in apple.split(";"):
            words = part.split()
            if words and words[0] == "container":
                words = words[1:]
            if len(words) == 1 and words[0].startswith("-"):
                # A global option: `container --help` must list it.
                code, text = runtime.help([])
                if code != 0 or not _lists_flag(text, words[0]):
                    return "missing"
                continue
            # A command (or sub-command) name: its own --help must exit 0.
            code, _ = runtime.help(words)
            if code != 0:
                return "missing"
        return "held"
    command = _flag_command(entry)
    if not command:
        return "not checked"
    code, text = runtime.help(command)
    if code != 0:
        return "missing"
    flag = apple.split()[0]  # `--ssh default` documents the flag `--ssh`
    return "held" if _lists_flag(text, flag) else "missing"


def _run_live(container: str) -> tuple:
    """Run the live tier; return (ran, {test id: outcome}, raw output, exit code)."""
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
    return (
        bool(outcomes) and any(v != "not run" for v in outcomes.values()),
        outcomes,
        result.stdout + result.stderr,
        result.returncode,
    )


def _verdict(skip_live: bool, live_ran: bool, not_run: list, problems: int) -> tuple:
    """(exit status, summary footer) for the run; a failure (1) wins over an
    incomplete live tier (3)."""
    live_incomplete = (not skip_live) and (not live_ran or bool(not_run))
    if problems:
        return 1, f"result: failed ({problems} problem{'s' if problems != 1 else ''})"
    if live_incomplete:
        if not live_ran:
            return 3, "result: incomplete (live tier: not run)"
        return 3, (
            f"result: incomplete ({len(not_run)} live entry"
            f"{'s' if len(not_run) != 1 else ''} without an executed test)"
        )
    detail = "live tier: not run (--skip-live)" if skip_live else "live tier: ran"
    return 0, f"result: success ({detail})"


def _git(args: list) -> str:
    """Stdout of ``git <args>`` in the repository; raises on a non-zero exit."""
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True
    )
    return result.stdout


def tree_hash(trees: dict) -> str:
    """One sha256 over the tree identifiers, in the order of ``EVIDENCE_TREES``.

    The verifier recomputes the same value from the tagged commit, so the two
    must agree on this exact text (design D8).
    """
    text = "\n".join(f"{name} {trees[name]}" for name in EVIDENCE_TREES)
    return hashlib.sha256(text.encode()).hexdigest()


def dirty_trees(git) -> list:
    """The evidence directories with tracked changes or untracked files."""
    return [name for name in EVIDENCE_TREES if git(["status", "--porcelain", "--", name]).strip()]


def live_counts(outcomes: dict, declared: int) -> dict:
    """The record of one live run.

    ``ran`` counts the tests that executed to a verdict; a skipped test is
    ``skipped`` and a declared test that never reported is ``not_run``.
    """
    passed = sum(1 for value in outcomes.values() if value == "passed")
    failed = sum(1 for value in outcomes.values() if value == "FAILED")
    skipped = sum(1 for value in outcomes.values() if value == "not run")
    return {
        "ran": passed + failed,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "not_run": max(declared - len(outcomes), 0),
        "declared": declared,
    }


def evidence_complete(live: dict) -> bool:
    """True only when every declared live test ran and passed."""
    return (
        live["declared"] > 0
        and live["ran"] == live["passed"] == live["declared"]
        and live["failed"] == live["skipped"] == live["not_run"] == 0
    )


def write_evidence(directory: Path, version: str, live: dict, git, now: datetime) -> Path:
    """Write the evidence file for ``HEAD`` and return its path."""
    trees = {name: git(["rev-parse", f"HEAD:{name}"]).strip() for name in EVIDENCE_TREES}
    record = {
        "schema": EVIDENCE_SCHEMA,
        "container_version": version,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": git(["rev-parse", "HEAD"]).strip(),
        "trees": trees,
        "tree_hash": tree_hash(trees),
        "live": live,
        "result": "passed",
    }
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"runtime-{version}-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def main(
    argv: list = None,
    *,
    runtime: "Runtime" = None,
    run_live=None,
    git=None,
    declared: int = None,
    now: datetime = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--container", default=os.environ.get("CONTAINER_DOCKER_SHIM_CONTAINER") or "container")
    parser.add_argument("--skip-live", action="store_true", help="only run the read-only matrix check")
    parser.add_argument("--output", help="also write the summary to this file")
    parser.add_argument(
        "--evidence",
        action="store_true",
        help="after a full successful live run, write the release evidence file",
    )
    parser.add_argument(
        "--evidence-dir", default=str(EVIDENCE_DIR), help="where --evidence writes (default docs/evidence)"
    )
    args = parser.parse_args(argv)
    if args.evidence and args.skip_live:
        print("--evidence needs the live tier: it cannot be combined with --skip-live", file=sys.stderr)
        return 2

    if runtime is None:
        runtime = Runtime(args.container)
    if run_live is None:
        run_live = _run_live

    try:
        version = runtime.version()
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"cannot run {args.container}: {exc}", file=sys.stderr)
        return 2
    if args.evidence:
        found = re.search(r"\d+\.\d+\.\d+", version or "")
        if not found or found.group(0) != SUPPORTED_CONTAINER_VERSION:
            print(
                f"--evidence needs the supported container release {SUPPORTED_CONTAINER_VERSION}, "
                f"found {version or 'unavailable'}",
                file=sys.stderr,
            )
            return 2
        git = git or _git
        dirty = dirty_trees(git)
        if dirty:
            print(
                "--evidence hashes HEAD, but these directories differ from it: "
                + ", ".join(dirty)
                + "; commit or discard the changes first",
                file=sys.stderr,
            )
            return 2
    lines = [
        f"candidate runtime : {version or 'unavailable'}",
        f"supported version : {SUPPORTED_CONTAINER_VERSION} (this script never changes it)",
    ]

    entries = compat_matrix.load()
    errors = compat_matrix.validate(entries)
    if errors:
        print("\n".join(["matrix is invalid:"] + errors), file=sys.stderr)
        print("result: usage (invalid matrix)", file=sys.stderr)
        return 2

    apple = {entry["id"]: check_entry(entry, runtime) for entry in entries}

    live_ran, outcomes, live_output, live_returncode = False, {}, "", 0
    if args.skip_live:
        lines.append("live tier         : not run (--skip-live)")
    else:
        live_ran, outcomes, live_output, live_returncode = run_live(args.container)
        if live_returncode:
            print("live test runner failed:", file=sys.stderr)
            print(live_output, file=sys.stderr, end="" if live_output.endswith("\n") else "\n")
            lines.append(f"live tier         : failed (runner exit {live_returncode})")
        else:
            lines.append("live tier         : " + ("ran" if live_ran else "not run (skipped or runtime unavailable)"))

    rows = ["", "| id | claimed | apple side | live test |", "| --- | --- | --- | --- |"]
    problems = int(bool(live_returncode))
    not_run = []
    for entry in entries:
        claimed = entry["evidence"]
        test = entry.get("test")
        live = "-"
        if test and claimed == "live":
            live = outcomes.get(test, "not run") if live_ran else "not run"
            if live == "FAILED":
                problems += 1
            elif live == "not run":
                not_run.append(entry["id"])
        if apple[entry["id"]] == "missing":
            problems += 1
        rows.append(f"| {entry['id']} | {claimed} | {apple[entry['id']]} | {live} |")

    lines += rows
    lines += ["", f"entries: {len(entries)}; problems: {problems}"]
    status, footer = _verdict(args.skip_live, live_ran, not_run, problems)
    if args.evidence and status == 0:
        live = live_counts(
            outcomes,
            len(test_report.declared_tests("live")) if declared is None else declared,
        )
        counts = ", ".join(f"{key} {value}" for key, value in live.items())
        if evidence_complete(live):
            path = write_evidence(
                Path(args.evidence_dir),
                SUPPORTED_CONTAINER_VERSION,
                live,
                git,
                now or datetime.now(timezone.utc),
            )
            lines.append(f"evidence          : written to {path} ({counts})")
        else:
            status = 3
            footer = "result: incomplete (live tier did not run every declared test)"
            lines.append(f"evidence          : not written ({counts})")
    elif args.evidence:
        lines.append("evidence          : not written (validation did not succeed)")
    lines.append(footer)
    text = "\n".join(lines)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
