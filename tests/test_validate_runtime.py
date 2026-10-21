"""Unit tests for ``scripts/validate-runtime.py`` (design D8).

The script lives outside the ``tests/`` package and its name has a hyphen, so
it is loaded from its file path with ``importlib.util`` (the repo has no
``tests/__init__.py`` and ``discover -s tests`` puts ``tests/`` on
``sys.path`` for the rest of the imports below). Every test injects a fake
runtime, a fake ``run_live`` and a synthetic matrix; no real ``container``
binary, no subprocess, no live tier.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


def _load_script_module():
    """Load ``scripts/validate-runtime.py`` as a module."""
    spec = importlib.util.spec_from_file_location(
        "validate_runtime", ROOT / "scripts" / "validate-runtime.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TIER = "unit"

validator = _load_script_module()


class FakeRuntime:
    """A candidate runtime that answers ``--version`` and ``--help`` from a table.

    ``help_results`` maps a ``--help`` command (a tuple of words) to
    ``(exit code, combined output)``; a command absent from the table answers
    ``(0, "")``.
    """

    def __init__(self, version: str = "1.5.0", help_results: dict = None) -> None:
        self._version = version
        self._help_results = dict(help_results or {})
        self.help_calls = []

    def version(self) -> str:
        return self._version

    def help(self, words) -> tuple:
        self.help_calls.append(tuple(words))
        return self._help_results.get(tuple(words), (0, ""))


def make_entry(entry_id: str, evidence: str, apple, test: str = None) -> dict:
    """A synthetic matrix entry with every schema field present."""
    return {
        "id": entry_id,
        "kind": "option",
        "status": "SUPPORTED",
        "apple": apple,
        "evidence": evidence,
        "test": test,
        "note": "synthetic entry",
    }


def make_run_live(ran: bool, outcomes: dict, returncode: int = 0):
    """A fake live tier with the real one's shape; records its calls."""

    def run_live(container: str) -> tuple:
        run_live.calls.append(container)
        return (ran, dict(outcomes), "", returncode)

    run_live.calls = []
    return run_live


def run_main(argv: list, runtime: FakeRuntime, run_live, entries: list) -> tuple:
    """Run ``main`` with injected fakes; return (exit status, stdout, stderr)."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with mock.patch.object(validator.compat_matrix, "load", return_value=entries):
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = validator.main(argv, runtime=runtime, run_live=run_live)
    return status, stdout.getvalue(), stderr.getvalue()


class ExitStatusTests(unittest.TestCase):
    """The exit status names the case (design D8); 1 wins over 3."""

    def test_every_live_test_skipped_exits_3(self):
        entries = [
            make_entry("run --live-flag", "live", "--live-flag", "t_live.LiveTests.test_x"),
            make_entry("run --other", "unit", "--other"),
        ]
        runtime = FakeRuntime(help_results={("run",): (0, "  --live-flag\n  --other\n")})
        # The suite ran but every live test was skipped (runtime down).
        run_live = make_run_live(False, {"t_live.LiveTests.test_x": "not run"})
        status, out, _ = run_main([], runtime, run_live, entries)
        self.assertEqual(status, 3)
        self.assertIn("live tier: not run", out)
        self.assertIn("result: incomplete (live tier: not run)", out)

    def test_live_entry_without_executed_test_exits_3(self):
        entries = [
            make_entry("run --live-flag", "live", "--live-flag", "t_live.LiveTests.test_missing")
        ]
        runtime = FakeRuntime(help_results={("run",): (0, "  --live-flag\n")})
        # The tier ran, but this entry's test is not among the outcomes.
        run_live = make_run_live(True, {"t_live.LiveTests.test_other": "passed"})
        status, out, _ = run_main([], runtime, run_live, entries)
        self.assertEqual(status, 3)
        self.assertIn("result: incomplete (1 live entry without an executed test)", out)

    def test_failed_live_test_exits_1(self):
        entries = [
            make_entry("run --live-flag", "live", "--live-flag", "t_live.LiveTests.test_x")
        ]
        runtime = FakeRuntime(help_results={("run",): (0, "  --live-flag\n")})
        run_live = make_run_live(True, {"t_live.LiveTests.test_x": "FAILED"}, returncode=1)
        status, out, err = run_main([], runtime, run_live, entries)
        self.assertEqual(status, 1)
        self.assertIn("result: failed", out)
        self.assertIn("live test runner failed", err)

    def test_missing_apple_flag_exits_1(self):
        entries = [make_entry("run --gone", "unit", "--gone")]
        # The candidate's --help no longer documents the flag.
        runtime = FakeRuntime(help_results={("run",): (0, "  --other\n")})
        run_live = make_run_live(False, {})
        status, out, _ = run_main(["--skip-live"], runtime, run_live, entries)
        self.assertEqual(status, 1)
        self.assertIn("result: failed (1 problem)", out)
        self.assertEqual(run_live.calls, [])

    def test_failed_and_incomplete_exits_1(self):
        entries = [
            make_entry("run --live-flag", "live", "--live-flag", "t_live.LiveTests.test_missing"),
            make_entry("run --gone", "unit", "--gone"),
        ]
        # 3: the live entry's test never ran. 1: --gone is missing. 1 wins.
        runtime = FakeRuntime(help_results={("run",): (0, "  --live-flag\n")})
        run_live = make_run_live(True, {"t_live.LiveTests.test_other": "passed"})
        status, out, _ = run_main([], runtime, run_live, entries)
        self.assertEqual(status, 1)
        self.assertIn("result: failed", out)

    def test_runner_error_exits_1(self):
        entries = [make_entry("run --flag", "unit", "--flag")]
        runtime = FakeRuntime(help_results={("run",): (0, "  --flag\n")})
        # The live suite crashed before any test ran: a runner error (1) and
        # an unrun tier (3) apply at once; 1 wins.
        run_live = make_run_live(False, {}, returncode=1)
        status, _, err = run_main([], runtime, run_live, entries)
        self.assertEqual(status, 1)
        self.assertIn("live test runner failed", err)

    def test_skip_live_without_problems_exits_0(self):
        entries = [
            make_entry("run --flag", "unit", "--flag"),
            make_entry("run --live-flag", "live", "--live-flag", "t_live.LiveTests.test_x"),
        ]
        runtime = FakeRuntime(help_results={("run",): (0, "  --flag\n  --live-flag\n")})
        run_live = make_run_live(False, {})
        status, out, _ = run_main(["--skip-live"], runtime, run_live, entries)
        self.assertEqual(status, 0)
        self.assertIn("not run (--skip-live)", out)
        self.assertIn("result: success (live tier: not run (--skip-live))", out)
        self.assertEqual(run_live.calls, [])

    def test_all_held_exits_0(self):
        entries = [
            make_entry("run --flag", "unit", "--flag"),
            make_entry("run --live-flag", "live", "--live-flag", "t_live.LiveTests.test_x"),
        ]
        runtime = FakeRuntime(help_results={("run",): (0, "  --flag\n  --live-flag\n")})
        run_live = make_run_live(True, {"t_live.LiveTests.test_x": "passed"})
        status, out, _ = run_main([], runtime, run_live, entries)
        self.assertEqual(status, 0)
        self.assertIn("result: success (live tier: ran)", out)
        self.assertEqual(len(run_live.calls), 1)

    def test_invalid_matrix_exits_2(self):
        bad = make_entry("run --flag", "unit", "--flag")
        del bad["note"]  # a missing schema field
        runtime = FakeRuntime()
        run_live = make_run_live(False, {})
        status, _, err = run_main([], runtime, run_live, [bad])
        self.assertEqual(status, 2)
        self.assertIn("matrix is invalid", err)


class CompoundApplePartsTests(unittest.TestCase):
    """An `apple` field of commands joined by ";" is checked part by part."""

    COMPOUND = "container --version; container system status"

    def check(self, runtime):
        return validator.check_entry(make_entry("docker version", "unit", self.COMPOUND), runtime)

    def test_every_part_holds(self):
        runtime = FakeRuntime(help_results={(): (0, "  --version  Show the version.\n")})
        self.assertEqual(self.check(runtime), "held")
        self.assertIn(("system", "status"), runtime.help_calls)

    def test_a_missing_command_part_is_missing(self):
        runtime = FakeRuntime(
            help_results={(): (0, "  --version\n"), ("system", "status"): (1, "unknown")}
        )
        self.assertEqual(self.check(runtime), "missing")

    def test_a_missing_global_option_part_is_missing(self):
        runtime = FakeRuntime(help_results={(): (0, "  --help\n")})
        self.assertEqual(self.check(runtime), "missing")

    def test_a_single_command_is_unchanged(self):
        runtime = FakeRuntime()
        entry = make_entry("volume create", "unit", "container volume create")
        self.assertEqual(validator.check_entry(entry, runtime), "held")
        self.assertEqual(runtime.help_calls, [("volume", "create")])


if __name__ == "__main__":
    unittest.main()
