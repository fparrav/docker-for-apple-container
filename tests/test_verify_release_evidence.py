"""Unit tests for ``scripts/verify_release_evidence.py`` (design D8).

Each test builds a throwaway git repository with the three evidence
directories, a ``cli.py`` carrying ``SUPPORTED_CONTAINER_VERSION`` and an
evidence directory, so the hash comes from real ``git rev-parse`` output. No
network, no runtime, no repository state outside the temporary directory.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TIER = "unit"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verifier = _load("verify_release_evidence", "verify_release_evidence.py")

NOW = datetime(2026, 10, 21, 14, 0, 0, tzinfo=timezone.utc)
VERSION = "1.5.0"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


class Repo:
    """A temporary repository shaped like this project."""

    def __init__(self, root: Path) -> None:
        self.root = root
        git(root, "init", "-q")
        (root / "src" / "container_docker_shim").mkdir(parents=True)
        (root / "src" / "container_docker_shim" / "cli.py").write_text(
            f'SUPPORTED_CONTAINER_VERSION = "{VERSION}"\n'
        )
        (root / "scripts").mkdir()
        (root / "scripts" / "tool.py").write_text("print('tool')\n")
        (root / "tests").mkdir()
        (root / "tests" / "test_x.py").write_text("x = 1\n")
        (root / "README.md").write_text("readme\n")
        (root / "pyproject.toml").write_text('[project]\nname = "x"\n')
        self.evidence_dir = root / "docs" / "evidence"
        self.commit("initial")

    def commit(self, message: str) -> None:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", message)

    def trees(self) -> dict:
        return {
            name: git(self.root, "rev-parse", f"HEAD:{name}").strip()
            for name in verifier.EVIDENCE_INPUTS
        }

    def write_evidence(self, *, at: datetime = NOW - timedelta(days=1), **changes) -> Path:
        """Write an evidence file for the current trees; ``changes`` override fields."""
        trees = self.trees()
        live = {"ran": 20, "passed": 20, "failed": 0, "skipped": 0, "not_run": 0, "declared": 20}
        live.update(changes.pop("live", {}))
        record = {
            "schema": 2,
            "container_version": VERSION,
            "generated_at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "commit": "0" * 40,
            "trees": trees,
            "tree_hash": verifier.tree_hash(trees),
            "live": live,
            "result": "passed",
        }
        record.update(changes)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        path = self.evidence_dir / f"runtime-{VERSION}-{at.strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(record))
        return path


class VerifierTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Repo(Path(directory.name))

    def run_main(self, *argv: str) -> tuple:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = verifier.main(["--repo", str(self.repo.root), *argv], now=NOW)
        return status, stdout.getvalue(), stderr.getvalue()

    def assert_rejected(self, tag: str = "v1.2.3", *, mentioning: str) -> str:
        status, out, err = self.run_main("--tag", tag)
        self.assertEqual(status, 3, out + err)
        self.assertIn(mentioning, err)
        return err


class FinalTagTests(VerifierTestCase):
    def test_matching_evidence_passes(self) -> None:
        self.repo.write_evidence()
        status, out, err = self.run_main("--tag", "v1.2.3")
        self.assertEqual(status, 0, err)
        self.assertIn("is valid", out)

    def test_version_differs(self) -> None:
        self.repo.write_evidence(container_version="1.4.0")
        err = self.assert_rejected(mentioning="1.4.0")
        self.assertIn(VERSION, err)

    def test_code_commit_after_the_evidence_invalidates_it(self) -> None:
        self.repo.write_evidence()
        self.repo.commit("evidence")
        (self.repo.root / "src" / "container_docker_shim" / "new.py").write_text("y = 2\n")
        self.repo.commit("code")
        err = self.assert_rejected(mentioning="tree hash")
        self.assertIn(verifier.tree_hash(self.repo.trees()), err)

    def test_readme_and_evidence_commits_keep_it_valid(self) -> None:
        self.repo.write_evidence()
        (self.repo.root / "README.md").write_text("changed\n")
        self.repo.commit("docs and evidence")
        status, _, err = self.run_main("--tag", "v1.2.3")
        self.assertEqual(status, 0, err)

    def test_a_packaging_commit_after_the_evidence_invalidates_it(self) -> None:
        self.repo.write_evidence()
        self.repo.commit("evidence")
        (self.repo.root / "pyproject.toml").write_text('[project]\nname = "x"\nrequires-python = ">=3.10"\n')
        self.repo.commit("packaging")
        err = self.assert_rejected(mentioning="tree hash")
        self.assertIn(verifier.tree_hash(self.repo.trees()), err)
        self.assertIn("pyproject.toml", err)

    def test_each_evidence_input_binds_the_hash(self) -> None:
        for relative in (
            "src/container_docker_shim/cli.py", "scripts/tool.py", "tests/test_x.py", "pyproject.toml",
        ):
            with self.subTest(changed=relative):
                self.repo.write_evidence()
                path = self.repo.root / relative
                path.write_text(path.read_text() + "# edit\n")
                self.repo.commit(f"edit {relative}")
                self.assert_rejected(mentioning="tree hash")
                for stale in self.repo.evidence_dir.glob("*.json"):
                    stale.unlink()

    def test_older_than_thirty_days(self) -> None:
        self.repo.write_evidence(at=NOW - timedelta(days=31))
        err = self.assert_rejected(mentioning="30 days")
        self.assertIn("2026-09-20", err)

    def test_exactly_thirty_days_is_still_fresh(self) -> None:
        self.repo.write_evidence(at=NOW - timedelta(days=30))
        status, _, err = self.run_main("--tag", "v1.2.3")
        self.assertEqual(status, 0, err)

    def test_max_age_days_option(self) -> None:
        self.repo.write_evidence(at=NOW - timedelta(days=10))
        status, _, err = self.run_main("--tag", "v1.2.3", "--max-age-days", "5")
        self.assertEqual(status, 3)
        self.assertIn("5 days", err)

    def test_dated_in_the_future(self) -> None:
        self.repo.write_evidence(at=NOW + timedelta(days=3))
        self.assert_rejected(mentioning="future")

    def test_nothing_ran(self) -> None:
        self.repo.write_evidence(live={"ran": 0, "passed": 0, "declared": 0})
        self.assert_rejected(mentioning="did not run completely")

    def test_skipped_not_run_and_failed_each_reject(self) -> None:
        cases = {
            "skipped": {"skipped": 1, "declared": 21},
            "not_run": {"not_run": 1, "declared": 21},
            "failed": {"failed": 1, "passed": 19},
        }
        for name, live in cases.items():
            with self.subTest(case=name):
                self.repo.write_evidence(live=live)
                err = self.assert_rejected(mentioning="did not run completely")
                self.assertIn(f"{name} 1", err)
                for stale in self.repo.evidence_dir.glob("*.json"):
                    stale.unlink()

    def test_ran_below_declared(self) -> None:
        self.repo.write_evidence(live={"declared": 21})
        self.assert_rejected(mentioning="did not run completely")

    def test_result_not_passed(self) -> None:
        self.repo.write_evidence(result="failed")
        self.assert_rejected(mentioning="'failed'")

    def test_no_evidence_says_how_to_produce_it(self) -> None:
        err = self.assert_rejected(mentioning="no evidence")
        self.assertIn("--evidence", err)

    def test_malformed_json_is_named_not_treated_as_absent(self) -> None:
        self.repo.evidence_dir.mkdir(parents=True)
        (self.repo.evidence_dir / "runtime-1.5.0-bad.json").write_text("{not json")
        err = self.assert_rejected(mentioning="runtime-1.5.0-bad.json")
        self.assertNotIn("no evidence", err)

    def test_missing_field_is_named(self) -> None:
        path = self.repo.write_evidence()
        record = json.loads(path.read_text())
        del record["tree_hash"]
        path.write_text(json.dumps(record))
        self.assert_rejected(mentioning="'tree_hash'")

    def test_counts_that_do_not_add_up(self) -> None:
        self.repo.write_evidence(live={"ran": 20, "passed": 19, "failed": 0})
        self.assert_rejected(mentioning="do not add up")

    def test_wrong_types_are_rejected(self) -> None:
        self.repo.write_evidence(live={"ran": True})
        self.assert_rejected(mentioning="live.ran")

    def test_tree_hash_that_does_not_match_its_trees(self) -> None:
        self.repo.write_evidence(tree_hash="f" * 64)
        self.assert_rejected(mentioning="its own trees")

    def test_earlier_schema_names_both_numbers_and_says_to_run_again(self) -> None:
        self.repo.write_evidence(schema=1)
        err = self.assert_rejected(mentioning="schema 1")
        self.assertIn("current schema is 2", err)
        self.assertIn("validate-runtime.py --evidence", err)

    def test_unknown_future_schema(self) -> None:
        self.repo.write_evidence(schema=3)
        self.assert_rejected(mentioning="schema 3")

    def test_the_shape_needs_the_packaging_id(self) -> None:
        trees = self.repo.trees()
        del trees["pyproject.toml"]
        self.repo.write_evidence(trees=trees)
        self.assert_rejected(mentioning="pyproject.toml")

    def test_the_newest_file_is_the_one_judged(self) -> None:
        self.repo.write_evidence(at=NOW - timedelta(days=5), live={"ran": 0, "passed": 0})
        self.repo.write_evidence(at=NOW - timedelta(days=1))
        status, out, err = self.run_main("--tag", "v1.2.3")
        self.assertEqual(status, 0, err)
        self.assertIn("20261020T140000Z", out)

    def test_a_newer_bad_file_wins_over_an_older_good_one(self) -> None:
        self.repo.write_evidence(at=NOW - timedelta(days=5))
        self.repo.write_evidence(at=NOW - timedelta(days=1), live={"skipped": 1, "declared": 21})
        self.assert_rejected(mentioning="did not run completely")


class PrereleaseTagTests(VerifierTestCase):
    def test_prerelease_tags_pass_without_reading_evidence(self) -> None:
        self.repo.evidence_dir.mkdir(parents=True)
        (self.repo.evidence_dir / "garbage.json").write_text("{not json")
        for tag in ("v1.2.3rc1", "v1.2.3a1", "v1.2.3b2", "v1.2.3.dev1"):
            with self.subTest(tag=tag):
                status, out, err = self.run_main("--tag", tag)
                self.assertEqual(status, 0, err)
                self.assertIn("not required", out)
                status, out, _ = self.run_main("--tag", tag, "--print-channel")
                self.assertEqual((status, out.strip()), (0, "prerelease"))

    def test_final_channel(self) -> None:
        status, out, _ = self.run_main("--tag", "v1.2.3", "--print-channel")
        self.assertEqual((status, out.strip()), (0, "final"))

    def test_unrecognised_tags_fail(self) -> None:
        for tag in ("v1.2.3-spike", "v1.2.3.post1", "1.2.3", "v1.2", "v1.2.3rc", "latest", "v1.2.3rc1x"):
            with self.subTest(tag=tag):
                err = self.assert_rejected(tag, mentioning="neither a final release")
                self.assertIn(tag, err)
                status, _, _ = self.run_main("--tag", tag, "--print-channel")
                self.assertEqual(status, 3)


class NotesTests(VerifierTestCase):
    def test_final_notes_cite_the_evidence(self) -> None:
        path = self.repo.write_evidence()
        status, out, err = self.run_main("--tag", "v1.2.3", "--notes")
        self.assertEqual(status, 0, err)
        self.assertIn(path.name, out)
        self.assertIn(f"container` version: {VERSION}", out)
        self.assertIn("2026-10-20T14:00:00Z", out)
        self.assertIn("ran 20, passed 20, failed 0, skipped 0, not_run 0, declared 20", out)
        self.assertIn("not signed", out)

    def test_final_notes_need_valid_evidence(self) -> None:
        status, out, err = self.run_main("--tag", "v1.2.3", "--notes")
        self.assertEqual(status, 3)
        self.assertEqual(out, "")
        self.assertIn("no evidence", err)

    def test_prerelease_notes_say_evidence_was_not_required(self) -> None:
        status, out, err = self.run_main("--tag", "v1.2.3rc1", "--notes")
        self.assertEqual(status, 0, err)
        self.assertIn("Live evidence was not required for this pre-release.", out)
        self.assertIn("v1.2.3rc1", out)


class SharedContractTests(unittest.TestCase):
    """The producer and the verifier must agree on the hash and the counts."""

    def test_tree_hash_matches_validate_runtime(self) -> None:
        producer = _load("validate_runtime_for_hash", "validate-runtime.py")
        trees = {"src": "a" * 40, "scripts": "b" * 40, "tests": "c" * 40, "pyproject.toml": "e" * 40}
        self.assertEqual(producer.tree_hash(trees), verifier.tree_hash(trees))
        text = "src %s\nscripts %s\ntests %s\npyproject.toml %s" % ("a" * 40, "b" * 40, "c" * 40, "e" * 40)
        self.assertEqual(verifier.tree_hash(trees), hashlib.sha256(text.encode()).hexdigest())

    def test_both_scripts_share_inputs_and_schema(self) -> None:
        producer = _load("validate_runtime_for_inputs", "validate-runtime.py")
        self.assertEqual(producer.EVIDENCE_TREES, verifier.EVIDENCE_TREES)
        self.assertEqual(producer.EVIDENCE_FILES, verifier.EVIDENCE_FILES)
        self.assertEqual(producer.EVIDENCE_INPUTS, verifier.EVIDENCE_INPUTS)
        self.assertEqual(producer.EVIDENCE_SCHEMA, verifier.EVIDENCE_SCHEMA)

    def test_a_record_written_by_validate_runtime_is_accepted(self) -> None:
        producer = _load("validate_runtime_for_record", "validate-runtime.py")
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(Path(directory))
            live = {"ran": 3, "passed": 3, "failed": 0, "skipped": 0, "not_run": 0, "declared": 3}
            producer.write_evidence(
                repo.evidence_dir, VERSION, live, lambda args: git(repo.root, *args), NOW - timedelta(days=2)
            )
            found = verifier.verify_final(repo.root, repo.evidence_dir, now=NOW)
            self.assertEqual(found.live["declared"], 3)

    def test_tag_is_read_only_from_the_argument(self) -> None:
        source = (ROOT / "scripts" / "verify_release_evidence.py").read_text()
        self.assertNotIn("os.environ", source)
        self.assertNotIn("urllib", source)
        self.assertNotIn("socket", source)


if __name__ == "__main__":
    unittest.main()
