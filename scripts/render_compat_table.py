#!/usr/bin/env python3
"""Render the compatibility matrix into the README block.

The README carries the compatibility matrix between the HTML comment markers
``<!-- compat-matrix:start -->`` and ``<!-- compat-matrix:end -->``. This
script regenerates that block from ``tests/compat/matrix.json`` so the matrix
and the README can never drift apart.

Usage:
    scripts/render_compat_table.py               print the block to stdout
    scripts/render_compat_table.py --write PATH  replace the block in PATH in place
    scripts/render_compat_table.py --check PATH  exit 1 if PATH is stale (quiet)

``--write`` exits 1 with a message on stderr when the markers are missing.
``--check`` prints nothing: it exits 0 only when the file already contains
exactly the rendered block.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import compat_matrix  # noqa: E402

START = "<!-- compat-matrix:start -->"
END = "<!-- compat-matrix:end -->"


def render_block(entries) -> str:
    """Return the full block: START marker, the table, then the END marker."""
    return f"{START}\n{compat_matrix.render_table(entries)}\n{END}"


def replace_block(text: str, block: str) -> str:
    """Replace the region between (and including) the markers in ``text``.

    Raises ValueError if either marker is missing or the end marker precedes
    the start marker.
    """
    start = text.find(START)
    end = text.find(END)
    if start == -1 or end == -1:
        raise ValueError(f"missing compat-matrix markers (want {START} ... {END})")
    if end < start:
        raise ValueError("compat-matrix end marker precedes the start marker")
    return text[:start] + block + text[end + len(END):]


def check_file(path: Path) -> int:
    """Exit 0 if ``path`` already contains exactly the rendered block."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 1
    block = render_block(compat_matrix.load())
    try:
        return 0 if replace_block(text, block) == text else 1
    except ValueError:
        return 1


def write_file(path: Path) -> int:
    """Rewrite the block in ``path`` in place; exit 1 if the markers are missing."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        return 1
    try:
        replaced = replace_block(text, render_block(compat_matrix.load()))
    except ValueError as exc:
        print(f"error: {exc} ({path})", file=sys.stderr)
        return 1
    path.write_text(replaced, encoding="utf-8")
    return 0


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(description="Render the compatibility matrix block")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", metavar="PATH", help="replace the block in PATH in place")
    group.add_argument("--check", metavar="PATH", help="exit 1 if PATH does not contain the current block")
    args = parser.parse_args(argv)

    if args.write:
        return write_file(Path(args.write))
    if args.check:
        return check_file(Path(args.check))
    sys.stdout.write(render_block(compat_matrix.load()) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
