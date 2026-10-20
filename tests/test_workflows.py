"""Static trust-boundary checks for the GitHub Actions workflows (design D6).

The standard library has no YAML parser, so the checks read each workflow
file as text:

- every workflow declares a top-level ``permissions:`` block with at least
  one entry,
- no workflow uses ``pull_request_target``,
- every ``uses:`` reference is pinned to a full 40-character lowercase
  commit SHA (a branch, tag, or short SHA is a violation),
- ``contents: write`` is granted only by the ``release`` job of
  ``release.yml``,
- ``ci.yml`` uses no self-hosted runner,
- ``runtime.yml`` is not triggered by ``pull_request``,
- no workflow references a package index (no ``pypi`` substring).

Every checker takes the workflow *text*, so the pin checker is also run
against a synthetic in-memory workflow with an unpinned ``uses:`` reference,
which must be rejected: that proves the helper actually inspects the text
instead of passing vacuously on the real files.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = ROOT / ".github" / "workflows"

TIER = "unit"

# An action reference is accepted only as ``owner/repo@<40 lowercase hex>``.
PINNED_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")


def _code_lines(text: str) -> list[tuple[int, str]]:
    """(1-based line number, raw line) for lines that are neither blank nor comments."""
    return [
        (number, line)
        for number, line in enumerate(text.splitlines(), 1)
        if line.strip() and not line.lstrip().startswith("#")
    ]


def check_top_level_permissions(text: str) -> list[str]:
    """Violations of the rule that every workflow declares top-level permissions."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not re.match(r"^permissions:\s*(#.*)?$", line):
            continue
        for follow in lines[index + 1 :]:
            if not follow.strip() or follow.lstrip().startswith("#"):
                continue
            if len(follow) - len(follow.lstrip()) >= 2:
                return []
            break
        return [f"line {index + 1}: top-level `permissions:` has no entries"]
    return ["no top-level `permissions:` key"]


def check_no_pull_request_target(text: str) -> list[str]:
    """Violations of the rule that no workflow uses ``pull_request_target``."""
    return [
        f"line {number}: uses the pull_request_target trigger"
        for number, line in enumerate(text.splitlines(), 1)
        if "pull_request_target" in line
    ]


def check_uses_pinned(text: str) -> list[str]:
    """Violations of the rule that every ``uses:`` is pinned to a full commit SHA."""
    violations: list[str] = []
    for number, line in _code_lines(text):
        match = re.match(r"^\s*-?\s*uses:\s*(\S+)", line)
        if match is None:
            continue
        ref = match.group(1).split("#", 1)[0].strip()
        if not PINNED_ACTION.match(ref):
            violations.append(
                f"line {number}: `uses: {ref}` is not pinned to a full "
                "40-character lowercase commit SHA"
            )
    return violations


def contents_write_grants(text: str) -> list[str]:
    """Names of the jobs (or ``<top-level>``) that grant ``contents: write``.

    The workflow is scanned as indented text: a 2-space key under ``jobs:``
    starts a job, a 0-space key ends it, and a ``permissions:`` block at
    either level scopes the ``contents:`` entries below it.
    """
    grants: list[str] = []
    scope: str | None = None
    in_jobs = False
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        if indent == 0:
            in_jobs = stripped.startswith("jobs:")
            scope = (
                "<top-level>" if re.match(r"^permissions:\s*(#.*)?$", stripped) else None
            )
            continue
        if in_jobs and indent == 2:
            match = re.match(r"^([A-Za-z0-9_-]+):", stripped)
            scope = match.group(1) if match is not None else "<job>"
            continue
        if scope is not None and re.match(r"^contents:\s*write\b", stripped):
            grants.append(scope)
    return grants


def trigger_keys(text: str) -> list[str]:
    """The workflow's top-level trigger keys (the ``on:`` block)."""
    triggers: list[str] = []
    in_on = False
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        if indent == 0:
            match = re.match(r"^on:\s*(\S+)?\s*(#.*)?$", stripped)
            in_on = match is not None
            if in_on and match.group(1) is not None:
                triggers.append(match.group(1))
            continue
        if in_on and indent == 2:
            match = re.match(r"^([A-Za-z0-9_-]+):", stripped)
            if match is not None:
                triggers.append(match.group(1))
    return triggers


def check_no_pull_request_trigger(text: str) -> list[str]:
    """Violations of the rule that the workflow is not triggered by ``pull_request``."""
    if "pull_request" in trigger_keys(text):
        return ["triggered by pull_request"]
    return []


def check_no_self_hosted(text: str) -> list[str]:
    """Violations of the rule that the workflow uses no self-hosted runner."""
    return [
        f"line {number}: uses a self-hosted runner"
        for number, line in enumerate(text.splitlines(), 1)
        if "self-hosted" in line
    ]


def check_no_pypi(text: str) -> list[str]:
    """Violations of the rule that no workflow references a package index."""
    return [
        f"line {number}: references a package index"
        for number, line in enumerate(text.splitlines(), 1)
        if "pypi" in line.lower()
    ]


class WorkflowFileTests(unittest.TestCase):
    """The committed workflows satisfy every trust-boundary rule."""

    EXPECTED_WORKFLOWS = ("ci.yml", "release.yml", "runtime.yml")

    def workflow_paths(self) -> list[Path]:
        return sorted(WORKFLOWS_DIR.glob("*.yml"))

    def test_expected_workflows_exist(self) -> None:
        names = [path.name for path in self.workflow_paths()]
        for expected in self.EXPECTED_WORKFLOWS:
            self.assertIn(expected, names)

    def test_every_workflow_declares_top_level_permissions(self) -> None:
        for path in self.workflow_paths():
            with self.subTest(workflow=path.name):
                self.assertEqual(
                    check_top_level_permissions(path.read_text(encoding="utf-8")),
                    [],
                )

    def test_no_workflow_uses_pull_request_target(self) -> None:
        for path in self.workflow_paths():
            with self.subTest(workflow=path.name):
                self.assertEqual(
                    check_no_pull_request_target(path.read_text(encoding="utf-8")),
                    [],
                )

    def test_every_uses_reference_is_pinned_to_a_full_sha(self) -> None:
        for path in self.workflow_paths():
            with self.subTest(workflow=path.name):
                self.assertEqual(check_uses_pinned(path.read_text(encoding="utf-8")), [])

    def test_only_the_release_job_grants_contents_write(self) -> None:
        for path in self.workflow_paths():
            with self.subTest(workflow=path.name):
                expected = ["release"] if path.name == "release.yml" else []
                self.assertEqual(
                    contents_write_grants(path.read_text(encoding="utf-8")),
                    expected,
                )

    def test_ci_yml_uses_no_self_hosted_runner(self) -> None:
        text = (WORKFLOWS_DIR / "ci.yml").read_text(encoding="utf-8")
        self.assertEqual(check_no_self_hosted(text), [])

    def test_runtime_yml_has_no_pull_request_trigger(self) -> None:
        text = (WORKFLOWS_DIR / "runtime.yml").read_text(encoding="utf-8")
        self.assertEqual(check_no_pull_request_trigger(text), [])

    def test_no_workflow_references_a_package_index(self) -> None:
        for path in self.workflow_paths():
            with self.subTest(workflow=path.name):
                self.assertEqual(check_no_pypi(path.read_text(encoding="utf-8")), [])


class SyntheticWorkflowTests(unittest.TestCase):
    """The pin checker rejects an unpinned ``uses:`` reference.

    The real workflows pass the same helper; this in-memory workflow must
    not, which proves the helper actually inspects the text instead of
    passing vacuously.
    """

    def test_unpinned_uses_reference_is_rejected(self) -> None:
        text = (
            "name: Synthetic\n"
            "on:\n"
            "  push:\n"
            "permissions:\n"
            "  contents: read\n"
            "jobs:\n"
            "  job:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Checkout\n"
            "        uses: actions/checkout@main\n"
        )
        violations = check_uses_pinned(text)
        self.assertTrue(violations)
        self.assertTrue(
            any("actions/checkout@main" in violation for violation in violations)
        )


if __name__ == "__main__":
    unittest.main()
