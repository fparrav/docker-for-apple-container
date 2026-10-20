from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(TESTS_DIR))

from container_docker_shim import cli  # noqa: E402
from option_semantics import RISKY_PAIRS, ROWS, OptionSemantics  # noqa: E402

TIER = "unit"


def _option_sets(command: str) -> frozenset[str]:
    names = (
        ("RUN_VALUE_OPTIONS", "RUN_BOOL_OPTIONS", "RUN_REFUSED_OPTIONS", "RUN_EXPLICIT_OPTIONS")
        if command == "run"
        else ("BUILD_VALUE_OPTIONS", "BUILD_BOOL_OPTIONS", "BUILD_NOOP_OPTIONS", "BUILD_REFUSED_OPTIONS", "BUILD_EXPLICIT_OPTIONS")
    )
    return frozenset().union(*(getattr(cli, name) for name in names))


def _short_alias_pairs() -> set[tuple[str, str]]:
    pairs = set()
    for command in ("run", "build"):
        pairs.update(
            (command, option)
            for option in _option_sets(command)
            if option.startswith("-") and not option.startswith("--")
        )
    return pairs


def _uncovered_pairs() -> list[tuple[str, str]]:
    covered = {(row.command, row.option) for row in ROWS}
    return sorted((_short_alias_pairs() | set(RISKY_PAIRS)) - covered)


class OptionSemanticsTests(unittest.TestCase):
    def test_every_risky_option_has_a_semantics_row(self):
        missing = _uncovered_pairs()
        self.assertEqual(missing, [], "missing option-semantics rows: " + ", ".join(f"{cmd} {opt}" for cmd, opt in missing))

    def test_new_short_alias_is_reported_until_it_has_a_row(self):
        with patch.object(cli, "RUN_EXPLICIT_OPTIONS", cli.RUN_EXPLICIT_OPTIONS | {"-Z"}):
            self.assertIn(("run", "-Z"), _uncovered_pairs())

    def test_every_row_is_declared_as_risky(self):
        declared = _short_alias_pairs() | set(RISKY_PAIRS)
        unneeded = sorted({(row.command, row.option) for row in ROWS} - declared)
        self.assertEqual(unneeded, [], "rows outside the reviewed risky set: " + ", ".join(f"{cmd} {opt}" for cmd, opt in unneeded))

    def test_rows_are_unique_and_complete(self):
        self.assertTrue(all(isinstance(row, OptionSemantics) for row in ROWS))
        pairs = [(row.command, row.option) for row in ROWS]
        self.assertEqual(len(pairs), len(set(pairs)), "duplicate option-semantics rows")
        for row in ROWS:
            with self.subTest(command=row.command, option=row.option):
                self.assertTrue(row.docker_meaning)
                self.assertTrue(row.apple_meaning)
                self.assertTrue(row.decision)
                self.assertTrue(row.evidence)


if __name__ == "__main__":
    unittest.main()
