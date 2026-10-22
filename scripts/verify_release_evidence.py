#!/usr/bin/env python3
"""Check that a release tag has fresh live evidence for the code it ships.

A final tag (``vX.Y.Z``) needs the newest file under ``docs/evidence/`` to be
complete, recent and bound to this exact code: the Apple ``container`` release
it ran on equals ``SUPPORTED_CONTAINER_VERSION``, the hash of the git trees of
``src/``, ``scripts/`` and ``tests/`` and of ``pyproject.toml`` equals the one at
the tagged commit, it is
at most ``--max-age-days`` old, and every declared live test ran and passed.
Pre-release tags (``rc``, ``a``, ``b``, ``dev``) need no evidence. Any other tag
fails. ``scripts/validate-runtime.py --evidence`` produces the file.

Usage:
    verify_release_evidence.py --tag vX.Y.Z [--max-age-days N] [--evidence-dir DIR]
    verify_release_evidence.py --tag vX.Y.Z --print-channel
    verify_release_evidence.py --tag vX.Y.Z --notes

Exit status:
    0  the tag is acceptable (or, with --print-channel/--notes, the output was printed)
    2  usage error
    3  the tag is not acceptable; one line on stderr names the first failed check

Only the standard library is used, and nothing leaves the machine: the tag is
read from the argument, the evidence from the working tree and the hash from
``git rev-parse``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_AGE_DAYS = 30
FUTURE_TOLERANCE = timedelta(days=1)
EVIDENCE_SCHEMA = 2
# Must match scripts/validate-runtime.py: the hash covers these, in this order.
EVIDENCE_TREES = ("src", "scripts", "tests")
EVIDENCE_FILES = ("pyproject.toml",)
EVIDENCE_INPUTS = EVIDENCE_TREES + EVIDENCE_FILES
COUNT_FIELDS = ("ran", "passed", "failed", "skipped", "not_run", "declared")
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"

FINAL_TAG = re.compile(r"^v\d+\.\d+\.\d+$")
PRERELEASE_TAG = re.compile(r"^v\d+\.\d+\.\d+(?:(?:a|b|rc)\d+|\.dev\d+)$")
VERSION_LINE = re.compile(r'^SUPPORTED_CONTAINER_VERSION\s*=\s*"([^"]+)"', re.MULTILINE)

NO_EVIDENCE_REQUIRED = "Live evidence was not required for this pre-release."


class EvidenceError(Exception):
    """The tag cannot be released; the message names the first failed check."""


class Evidence:
    """One validated evidence file."""

    def __init__(self, path: Path, record: dict, generated_at: datetime) -> None:
        self.path = path
        self.record = record
        self.generated_at = generated_at

    @property
    def live(self) -> dict:
        return self.record["live"]


def classify_tag(tag: str) -> str:
    """``final`` or ``prerelease``; anything else is not a release tag."""
    if FINAL_TAG.match(tag):
        return "final"
    if PRERELEASE_TAG.match(tag):
        return "prerelease"
    raise EvidenceError(
        f"tag {tag!r} is neither a final release (vX.Y.Z) nor a recognised "
        "pre-release (vX.Y.Zrc1, a1, b1 or .dev1)"
    )


def supported_version(repo: Path) -> str:
    """``SUPPORTED_CONTAINER_VERSION`` read from the text of ``cli.py``."""
    source = (repo / "src" / "container_docker_shim" / "cli.py").read_text(encoding="utf-8")
    found = VERSION_LINE.search(source)
    if not found:
        raise EvidenceError("SUPPORTED_CONTAINER_VERSION not found in src/container_docker_shim/cli.py")
    return found.group(1)


def tree_hash(trees: dict) -> str:
    """One sha256 over ``<path> <git id>`` lines, in the order of ``EVIDENCE_INPUTS``."""
    text = "\n".join(f"{name} {trees[name]}" for name in EVIDENCE_INPUTS)
    return hashlib.sha256(text.encode()).hexdigest()


def head_trees(repo: Path) -> dict:
    """The git identifiers of the evidence directories and files at ``HEAD``."""
    trees = {}
    for name in EVIDENCE_INPUTS:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", f"HEAD:{name}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise EvidenceError(f"cannot read the git id of {name} at HEAD: {result.stderr.strip()}")
        trees[name] = result.stdout.strip()
    return trees


def _is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def load_file(path: Path) -> Evidence:
    """Parse and validate the shape of one evidence file."""
    name = path.name
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvidenceError(f"{name} is not readable JSON: {exc}")
    if not isinstance(record, dict):
        raise EvidenceError(f"{name} is not a JSON object")
    for field in ("schema", "container_version", "generated_at", "commit", "trees", "tree_hash", "live", "result"):
        if field not in record:
            raise EvidenceError(f"{name} lacks the field {field!r}")
    if record["schema"] != EVIDENCE_SCHEMA or isinstance(record["schema"], bool):
        raise EvidenceError(
            f"{name} has schema {record['schema']!r}, but the current schema is {EVIDENCE_SCHEMA}; "
            "run `python3 scripts/validate-runtime.py --evidence` again"
        )
    for field in ("container_version", "generated_at", "commit", "tree_hash", "result"):
        if not isinstance(record[field], str) or not record[field]:
            raise EvidenceError(f"{name}: {field!r} must be a non-empty string")
    trees = record["trees"]
    if not isinstance(trees, dict) or any(not isinstance(trees.get(d), str) or not trees[d] for d in EVIDENCE_INPUTS):
        raise EvidenceError(f"{name}: 'trees' must name {', '.join(EVIDENCE_INPUTS)}")
    live = record["live"]
    if not isinstance(live, dict):
        raise EvidenceError(f"{name}: 'live' must be an object")
    for field in COUNT_FIELDS:
        if not _is_count(live.get(field)):
            raise EvidenceError(f"{name}: live.{field} must be a non-negative integer")
    try:
        generated_at = datetime.strptime(record["generated_at"], TIMESTAMP).replace(tzinfo=timezone.utc)
    except ValueError:
        raise EvidenceError(f"{name}: generated_at {record['generated_at']!r} is not a UTC time like 2026-10-21T14:03:00Z")
    if live["ran"] != live["passed"] + live["failed"]:
        raise EvidenceError(
            f"{name}: counts do not add up (ran {live['ran']} != passed {live['passed']} + failed {live['failed']})"
        )
    if record["tree_hash"] != tree_hash(trees):
        raise EvidenceError(f"{name}: tree_hash does not match its own trees")
    return Evidence(path, record, generated_at)


def newest_evidence(directory: Path) -> Evidence:
    """The validated file with the latest ``generated_at``."""
    paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if not paths:
        raise EvidenceError(
            f"no evidence under {directory}; run `python3 scripts/validate-runtime.py --evidence` "
            "on a macOS 26 machine with the supported container release, commit the file, then tag"
        )
    files = [load_file(path) for path in paths]
    return max(files, key=lambda item: (item.generated_at, item.path.name))


def verify_final(
    repo: Path,
    directory: Path,
    *,
    max_age_days: int = MAX_AGE_DAYS,
    now: datetime | None = None,
) -> Evidence:
    """Judge the newest evidence against the checked-out commit; raise on the first failure."""
    now = now or datetime.now(timezone.utc)
    found = newest_evidence(directory)
    record, live, name = found.record, found.live, found.path.name
    if record["result"] != "passed":
        raise EvidenceError(f"{name} records result {record['result']!r}, not 'passed'")
    supported = supported_version(repo)
    if record["container_version"] != supported:
        raise EvidenceError(
            f"{name} was produced on container {record['container_version']}, "
            f"but SUPPORTED_CONTAINER_VERSION is {supported}"
        )
    current = tree_hash(head_trees(repo))
    if record["tree_hash"] != current:
        raise EvidenceError(
            f"{name} covers tree hash {record['tree_hash']}, but src/, scripts/, tests/ and pyproject.toml "
            f"at the tagged commit hash to {current}; run the validation again"
        )
    if found.generated_at > now + FUTURE_TOLERANCE:
        raise EvidenceError(f"{name} is dated {record['generated_at']}, which is in the future")
    if now - found.generated_at > timedelta(days=max_age_days):
        raise EvidenceError(
            f"{name} is dated {record['generated_at']}, older than the limit of {max_age_days} days"
        )
    if (
        live["ran"] == 0
        or live["failed"]
        or live["skipped"]
        or live["not_run"]
        or live["ran"] != live["passed"]
        or live["ran"] != live["declared"]
    ):
        raise EvidenceError(f"{name}: the live tier did not run completely ({_counts(live)})")
    return found


def _counts(live: dict) -> str:
    return ", ".join(f"{field} {live[field]}" for field in COUNT_FIELDS)


def notes_for(tag: str, found: Evidence | None) -> str:
    """The Markdown section the release notes carry."""
    if found is None:
        return f"## Live runtime evidence\n\n{NO_EVIDENCE_REQUIRED} (`{tag}`)\n"
    record = found.record
    return (
        "## Live runtime evidence\n\n"
        f"- Evidence file: `docs/evidence/{found.path.name}`\n"
        f"- Apple `container` version: {record['container_version']}\n"
        f"- Generated: {record['generated_at']} (UTC)\n"
        f"- Live tests: {_counts(found.live)}\n"
        f"- Code covered: `src/`, `scripts/`, `tests/` and `pyproject.toml` (hash `{record['tree_hash'][:12]}`)\n\n"
        "The evidence is a maintainer's attestation tied to that code; the file is not signed.\n"
    )


def main(argv: list | None = None, *, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tag", required=True, help="the release tag, for example v0.4.0")
    parser.add_argument("--max-age-days", type=int, default=MAX_AGE_DAYS)
    parser.add_argument("--evidence-dir", default=None, help="default: docs/evidence in the repository")
    parser.add_argument("--repo", default=str(ROOT), help="the repository root (default: this one)")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--print-channel", action="store_true", help="print 'final' or 'prerelease'")
    output.add_argument("--notes", action="store_true", help="print the release-notes section")
    args = parser.parse_args(argv)
    repo = Path(args.repo)
    directory = Path(args.evidence_dir) if args.evidence_dir else repo / "docs" / "evidence"

    try:
        channel = classify_tag(args.tag)
        if args.print_channel:
            print(channel)
            return 0
        found = None
        if channel == "final":
            found = verify_final(repo, directory, max_age_days=args.max_age_days, now=now)
        if args.notes:
            print(notes_for(args.tag, found), end="")
        elif found is None:
            print(f"{args.tag}: pre-release; {NO_EVIDENCE_REQUIRED}")
        else:
            print(f"{args.tag}: evidence {found.path.name} is valid ({_counts(found.live)})")
        return 0
    except EvidenceError as exc:
        print(f"release evidence: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
