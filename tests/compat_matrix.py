"""Compatibility matrix helpers for the Apple ``container`` runtime.

The matrix (``tests/compat/matrix.json``) records which Docker CLI commands,
options and Compose keys the shim translates, the Apple flag used, and the
strongest level of evidence collected for each entry. This module only loads,
validates and renders that data; it owns no runtime state.
"""

from __future__ import annotations

import json
from pathlib import Path


MATRIX_PATH = Path(__file__).resolve().parent / "compat" / "matrix.json"

KINDS = ("command", "option", "compose-key")
STATUSES = ("SUPPORTED", "PARTIAL", "UNSUPPORTED")
EVIDENCE = ("unit", "fake", "live", "conformance")
EVIDENCE_WITH_TEST = ("live", "conformance")
FIELDS = ("id", "kind", "status", "apple", "evidence", "test", "note")


def load() -> list:
    """Read ``matrix.json`` and return its list of entries."""
    with open(MATRIX_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _allowed(allowed: tuple) -> str:
    return ", ".join(allowed)


def validate(entries) -> list:
    """Return a list of human-readable schema errors for ``entries``."""
    if not isinstance(entries, list):
        return ["entries is not a list"]

    errors: list = []
    seen = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"entries[{index}] is not a mapping")
            continue

        entry_id = entry.get("id")
        label = entry_id if isinstance(entry_id, str) else f"#{index}"

        for field in FIELDS:
            if field not in entry:
                errors.append(
                    f"entries[{index}] ({label}): missing field '{field}'"
                )
        for key in entry:
            if key not in FIELDS:
                errors.append(
                    f"entries[{index}] ({label}): unknown field '{key}'"
                )

        if "kind" in entry and entry["kind"] not in KINDS:
            errors.append(
                f"entries[{index}] ({label}): kind {entry['kind']!r} is not "
                f"one of {_allowed(KINDS)}"
            )
        if "status" in entry and entry["status"] not in STATUSES:
            errors.append(
                f"entries[{index}] ({label}): status {entry['status']!r} is "
                f"not one of {_allowed(STATUSES)}"
            )
        if "evidence" in entry and entry["evidence"] not in EVIDENCE:
            errors.append(
                f"entries[{index}] ({label}): evidence {entry['evidence']!r} "
                f"is not one of {_allowed(EVIDENCE)}"
            )

        if entry_id is not None:
            if entry_id in seen:
                errors.append(
                    f"entries[{index}] ({label}): duplicate id {entry_id!r}"
                )
            else:
                seen.add(entry_id)

        if entry.get("evidence") in EVIDENCE_WITH_TEST and not entry.get("test"):
            errors.append(
                f"entries[{index}] ({label}): evidence "
                f"{entry['evidence']!r} requires a test id"
            )

    return errors


def _cell(value) -> str:
    text = "" if value is None else str(value).strip()
    return text.replace("|", "\\|") if text else "-"


def render_table(entries) -> str:
    """Return a Markdown table of entries sorted by kind then id."""
    lines = [
        "| id | status | apple | evidence | note |",
        "| --- | --- | --- | --- | --- |",
    ]
    ordered = sorted(
        entries,
        key=lambda entry: (
            entry.get("kind") if isinstance(entry, dict) else "",
            entry.get("id") if isinstance(entry, dict) else "",
        ),
    )
    for entry in ordered:
        fields = entry if isinstance(entry, dict) else {}
        cells = [
            _cell(fields.get("id")),
            _cell(fields.get("status")),
            _cell(fields.get("apple")),
            _cell(fields.get("evidence")),
            _cell(fields.get("note")),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
