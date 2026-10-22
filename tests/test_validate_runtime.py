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
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
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


TREES = {"src": "a" * 40, "scripts": "b" * 40, "tests": "c" * 40, "pyproject.toml": "e" * 40}
HEAD = "d" * 40
NOW = datetime(2026, 10, 21, 14, 3, 0, tzinfo=timezone.utc)
LIVE_ENTRY = make_entry("run --live-flag", "live", "--live-flag", "t_live.LiveTests.test_x")


def fake_git(dirty: tuple = ()):
    """A ``git`` stand-in: clean unless a directory is listed in ``dirty``."""

    def git(args: list) -> str:
        if args[:2] == ["status", "--porcelain"]:
            return " M changed.py\n" if args[-1] in dirty else ""
        if args[0] == "rev-parse" and args[1] == "HEAD":
            return HEAD + "\n"
        if args[0] == "rev-parse" and args[1].startswith("HEAD:"):
            return TREES[args[1][len("HEAD:"):]] + "\n"
        raise AssertionError(f"unexpected git call: {args}")

    return git


class EvidenceTests(unittest.TestCase):
    """``--evidence`` writes the release record only after a full success (design D8)."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.out = Path(self.directory.name) / "evidence"

    def evidence(self, outcomes: dict, *, version: str = "1.5.0", declared: int = None,
                 extra: list = (), dirty: tuple = (), returncode: int = 0):
        runtime = FakeRuntime(
            version=f"container CLI version {version} (build: release)",
            help_results={("run",): (0, "  --live-flag\n")},
        )
        run_live = make_run_live(bool(outcomes), outcomes, returncode=returncode)
        entries = [LIVE_ENTRY]
        argv = ["--evidence", "--evidence-dir", str(self.out), *extra]
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(validator.compat_matrix, "load", return_value=entries):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = validator.main(
                    argv,
                    runtime=runtime,
                    run_live=run_live,
                    git=fake_git(dirty),
                    declared=len(outcomes) if declared is None else declared,
                    now=NOW,
                )
        return status, stdout.getvalue(), stderr.getvalue(), run_live

    def files(self) -> list:
        return sorted(self.out.glob("*.json")) if self.out.exists() else []

    def test_full_success_writes_one_file_with_the_record(self):
        status, out, _, _ = self.evidence({"t_live.LiveTests.test_x": "passed"})
        self.assertEqual(status, 0, out)
        (path,) = self.files()
        self.assertEqual(path.name, "runtime-1.5.0-20261021T140300Z.json")
        record = json.loads(path.read_text())
        self.assertEqual(record["schema"], 2)
        self.assertEqual(record["container_version"], "1.5.0")
        self.assertEqual(record["generated_at"], "2026-10-21T14:03:00Z")
        self.assertEqual(record["commit"], HEAD)
        self.assertEqual(record["trees"], TREES)
        self.assertEqual(record["result"], "passed")
        self.assertEqual(
            record["live"],
            {"ran": 1, "passed": 1, "failed": 0, "skipped": 0, "not_run": 0, "declared": 1},
        )
        self.assertIn("evidence          : written to", out)
        self.assertTrue(out.rstrip().endswith("result: success (live tier: ran)"))

    def test_tree_hash_is_the_sha256_of_the_four_input_lines(self):
        self.evidence({"t_live.LiveTests.test_x": "passed"})
        (path,) = self.files()
        text = (
            f"src {TREES['src']}\nscripts {TREES['scripts']}\ntests {TREES['tests']}"
            f"\npyproject.toml {TREES['pyproject.toml']}"
        )
        self.assertEqual(
            json.loads(path.read_text())["tree_hash"], hashlib.sha256(text.encode()).hexdigest()
        )
        self.assertEqual(validator.tree_hash(TREES), hashlib.sha256(text.encode()).hexdigest())

    def test_counts_add_up(self):
        outcomes = {f"t.T.test_{n}": "passed" for n in range(2)}
        outcomes["t_live.LiveTests.test_x"] = "passed"
        self.evidence(outcomes)
        (path,) = self.files()
        live = json.loads(path.read_text())["live"]
        self.assertEqual(live["ran"], live["passed"] + live["failed"])
        self.assertEqual(live["declared"], live["ran"])

    def test_a_failed_test_writes_nothing_and_keeps_the_status(self):
        outcomes = {"t_live.LiveTests.test_x": "passed", "t_live.LiveTests.test_y": "FAILED"}
        status, out, _, _ = self.evidence(outcomes, returncode=1)
        self.assertEqual(status, 1)
        self.assertEqual(self.files(), [])
        self.assertIn("evidence          : not written", out)

    def test_a_skipped_test_writes_nothing(self):
        outcomes = {"t_live.LiveTests.test_x": "passed", "t_live.LiveTests.test_y": "not run"}
        status, out, _, _ = self.evidence(outcomes)
        self.assertEqual(self.files(), [])
        self.assertNotEqual(status, 0, out)

    def test_nothing_ran_writes_nothing(self):
        status, _, _, _ = self.evidence({}, declared=1)
        self.assertEqual(self.files(), [])
        self.assertEqual(status, 3)

    def test_a_declared_test_that_never_reported_writes_nothing(self):
        status, out, _, _ = self.evidence({"t_live.LiveTests.test_x": "passed"}, declared=2)
        self.assertEqual(status, 3)
        self.assertEqual(self.files(), [])
        self.assertIn("not_run 1", out)
        self.assertTrue(out.rstrip().endswith("result: incomplete (live tier did not run every declared test)"))

    def test_skip_live_with_evidence_is_a_usage_error(self):
        status, _, err, run_live = self.evidence(
            {"t_live.LiveTests.test_x": "passed"}, extra=["--skip-live"]
        )
        self.assertEqual(status, 2)
        self.assertIn("--skip-live", err)
        self.assertEqual(self.files(), [])
        self.assertEqual(run_live.calls, [])

    def test_changing_only_the_packaging_file_changes_the_hash(self):
        changed = dict(TREES, **{"pyproject.toml": "f" * 40})
        self.assertNotEqual(validator.tree_hash(TREES), validator.tree_hash(changed))
        for name in ("src", "scripts", "tests"):
            self.assertNotEqual(validator.tree_hash(TREES), validator.tree_hash(dict(TREES, **{name: "f" * 40})))

    def test_documentation_and_license_are_not_inputs(self):
        self.assertEqual(validator.EVIDENCE_INPUTS, ("src", "scripts", "tests", "pyproject.toml"))
        self.assertNotIn("README.md", validator.EVIDENCE_INPUTS)
        self.assertNotIn("LICENSE", validator.EVIDENCE_INPUTS)

    def test_the_record_names_the_four_ids(self):
        self.evidence({"t_live.LiveTests.test_x": "passed"})
        (path,) = self.files()
        self.assertEqual(
            sorted(json.loads(path.read_text())["trees"]), ["pyproject.toml", "scripts", "src", "tests"]
        )

    def test_a_dirty_pyproject_refuses_naming_it(self):
        status, _, err, run_live = self.evidence({"t_live.LiveTests.test_x": "passed"}, dirty=("pyproject.toml",))
        self.assertEqual(status, 2)
        self.assertIn("pyproject.toml", err)
        self.assertNotIn("src", err)
        self.assertEqual(self.files(), [])
        self.assertEqual(run_live.calls, [])

    def test_a_dirty_directory_refuses_before_running_anything(self):
        for name in ("src", "scripts", "tests"):
            with self.subTest(directory=name):
                status, _, err, run_live = self.evidence(
                    {"t_live.LiveTests.test_x": "passed"}, dirty=(name,)
                )
                self.assertEqual(status, 2)
                self.assertIn(name, err)
                self.assertEqual(self.files(), [])
                self.assertEqual(run_live.calls, [])

    def test_only_the_dirty_directory_is_named(self):
        _, _, err, _ = self.evidence({"t_live.LiveTests.test_x": "passed"}, dirty=("scripts",))
        self.assertIn("scripts", err)
        self.assertNotIn("src", err)

    def test_a_runtime_other_than_the_supported_one_refuses(self):
        status, _, err, run_live = self.evidence({"t_live.LiveTests.test_x": "passed"}, version="9.9.9")
        self.assertEqual(status, 2)
        self.assertIn("9.9.9", err)
        self.assertIn(validator.SUPPORTED_CONTAINER_VERSION, err)
        self.assertEqual(self.files(), [])
        self.assertEqual(run_live.calls, [])

    def test_without_the_flag_nothing_is_written(self):
        entries = [LIVE_ENTRY]
        runtime = FakeRuntime(help_results={("run",): (0, "  --live-flag\n")})
        run_live = make_run_live(True, {"t_live.LiveTests.test_x": "passed"})
        with mock.patch.object(validator.compat_matrix, "load", return_value=entries):
            with contextlib.redirect_stdout(io.StringIO()):
                status = validator.main(
                    ["--evidence-dir", str(self.out)], runtime=runtime, run_live=run_live
                )
        self.assertEqual(status, 0)
        self.assertEqual(self.files(), [])


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
