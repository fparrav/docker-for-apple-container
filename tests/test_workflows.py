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
- the conformance job of ``ci.yml`` passes ``--require conformance`` (the
  report step fails, exit 3, when any conformance test is not run, skipped,
  or missing) and hard-codes no number of tests,
- ``runtime.yml`` is not triggered by ``pull_request``,
- ``ci.yml`` builds and exercises the wheel in a ``wheel`` job on the Python
  floor and ``3.x``, and ``release.yml`` builds once in ``package``, tests those
  files in ``smoke`` and publishes them from ``release`` without building again,
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


def job_text(text: str, job: str) -> str:
    """The text of one job under the top-level ``jobs:`` block.

    From the ``  <job>:`` line (2-space indent) up to the next 2-space-indent
    key (or a 0-space top-level key) or the end of the file.
    """
    lines = text.splitlines()
    in_jobs = False
    start = None
    for index, line in enumerate(lines):
        if not in_jobs:
            if re.match(r"^jobs:\s*(#.*)?$", line):
                in_jobs = True
            continue
        if re.match(rf"^  {re.escape(job)}:\s*(#.*)?$", line):
            start = index
            break
    if start is None:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= 2:
            end = index
            break
    return "\n".join(lines[start:end])


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


def check_conformance_require(text: str) -> list[str]:
    """Violations of the rule that the conformance job requires its tests.

    The report step must pass ``--require conformance`` (exit 3 when any
    conformance test is not run, skipped, or missing) and keep
    ``if: always()`` on the later report-recording step, and the workflow
    must not hard-code a number of tests (no ``--expect``/``--count`` flag,
    no digit count next to the word "tests").
    """
    violations: list[str] = []
    job = job_text(text, "conformance")
    if "--require conformance" not in job:
        violations.append(
            "conformance job does not pass `--require conformance` to the report"
        )
    if "if: always()" not in job:
        violations.append(
            "conformance job is missing `if: always()` on the report-recording step"
        )
    for number, line in enumerate(text.splitlines(), 1):
        if re.search(r"--(expect|count)\b", line) or re.search(
            r"\b\d+\s+tests\b", line
        ):
            violations.append(f"line {number}: hard-codes a number of tests")
    return violations


def check_evidence_gate(text: str) -> list[str]:
    """Violations of the release evidence rules (design D9) in ``release.yml``.

    The verifier runs in ``checks`` with the tag passed through ``env`` (never
    inline in the shell line), ``checks`` keeps ``contents: read``, and the
    ``release`` job derives the channel, cites the evidence in the notes and
    hands ``prerelease`` to the release action.
    """
    violations: list[str] = []
    checks = job_text(text, "checks")
    release = job_text(text, "release")
    if "verify_release_evidence.py --tag" not in checks:
        violations.append("checks job does not run the release evidence verifier")
    if "TAG: ${{ github.ref_name }}" not in checks:
        violations.append("checks job does not pass the tag through env")
    if "contents: read" not in checks or "contents: write" in checks:
        violations.append("checks job does not keep contents: read")
    for number, line in enumerate(text.splitlines(), 1):
        if "verify_release_evidence.py" in line and "${{" in line:
            violations.append(f"line {number}: interpolates an expression into the shell line")
    if "--print-channel" not in release:
        violations.append("release job does not compute the channel")
    if "--notes" not in release:
        violations.append("release job does not cite the evidence in the notes")
    if "prerelease: ${{ steps.channel.outputs.prerelease }}" not in release:
        violations.append("release job does not pass prerelease to the release action")
    return violations


def python_floor() -> str:
    """The lowest Python minor version ``pyproject.toml`` declares (``>=3.9`` -> ``3.9``)."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^requires-python\s*=\s*"[^"]*?>=\s*(\d+\.\d+)', text, re.MULTILINE)
    if match is None:
        raise AssertionError("pyproject.toml declares no requires-python floor")
    return match.group(1)


def matrix_legs(job: str) -> list[dict[str, str]]:
    """The ``include:`` legs of the job's matrix as ``{"python": ..., "os": ...}``."""
    legs: list[dict[str, str]] = []
    for _, line in _code_lines(job):
        stripped = line.strip()
        match = re.match(r'^-\s*python:\s*"([^"]+)"', stripped)
        if match is not None:
            legs.append({"python": match.group(1), "os": ""})
            continue
        match = re.match(r"^os:\s*(\S+)", stripped)
        if match is not None and legs:
            legs[-1]["os"] = match.group(1)
    return legs


def run_lines(job: str) -> list[str]:
    """The lines of every ``run:`` step (inline command and block scalar body)."""
    collected: list[str] = []
    run_indent: int | None = None
    for _, line in _code_lines(job):
        indent = len(line) - len(line.lstrip())
        if run_indent is not None and indent > run_indent:
            collected.append(line)
            continue
        run_indent = None
        if re.match(r"^\s*-?\s*run:", line):
            collected.append(line)
            run_indent = indent
    return collected


def check_wheel_matrix(body: str, job: str, floor: str) -> list[str]:
    """Violations of the matrix rules shared by the CI and release wheel jobs (design D3)."""
    violations: list[str] = []
    legs = matrix_legs(body)
    newest = [leg for leg in legs if leg["python"] == "3.x"]
    if len(legs) != 3 or len(newest) != 2 or sum(leg["python"] == floor for leg in legs) != 1:
        violations.append(f"{job} does not run on the floor {floor} and 3.x")
    for leg in legs:
        if leg["python"] == floor and (not leg["os"] or leg["os"].endswith("-latest")):
            violations.append(f"{job} floor leg is not pinned to a versioned image")
    if sorted(leg["os"] for leg in newest) != ["macos-latest", "ubuntu-latest"]:
        violations.append(f"{job} 3.x legs do not run on ubuntu-latest and macos-latest")
    if not re.search(r"^\s*runs-on:\s*\$\{\{ matrix\.os \}\}\s*$", body, re.MULTILINE):
        violations.append(f"{job} does not select the runner from the matrix")
    if any("matrix.os" in line or "matrix.python" in line for line in run_lines(body)):
        violations.append(f"{job} interpolates the matrix into a run line")
    return violations


def check_wheel_job(text: str, job: str, floor: str) -> list[str]:
    """Violations of the rules for a job that builds and exercises the wheel (design D3, D8)."""
    body = job_text(text, job)
    if not body:
        return [f"no `{job}` job"]
    violations: list[str] = []
    if "contents: write" in body:
        violations.append(f"{job} grants contents: write")
    if "self-hosted" in body:
        violations.append(f"{job} uses a self-hosted runner")
    violations.extend(check_wheel_matrix(body, job, floor))
    if "python-version: ${{ matrix.python }}" not in body:
        violations.append(f"{job} does not select Python from the matrix")
    if "scripts/verify_wheel.py --dist dist" not in body:
        violations.append(f"{job} does not run verify_wheel.py on dist")
    return violations


def check_legs_match(ci_text: str, release_text: str) -> list[str]:
    """Violations of the rule that the smoke legs equal the wheel legs (design D4).

    The two matrices are copies (Actions has no shared includes), so this
    compares the ``include:`` legs of the ``wheel`` job of ``ci.yml`` with
    those of the ``smoke`` job of ``release.yml`` and reports any drift.
    """
    wheel = matrix_legs(job_text(ci_text, "wheel"))
    smoke = matrix_legs(job_text(release_text, "smoke"))
    if wheel == smoke:
        return []
    return [f"smoke legs {smoke} differ from the wheel legs {wheel}"]


def artifact_names(job: str, action: str) -> list[str]:
    """The ``name:`` each ``actions/<action>`` step of the job passes in its ``with:`` block."""
    names: list[str] = []
    in_step = False
    for _, line in _code_lines(job):
        stripped = line.strip()
        if stripped.startswith("- "):
            in_step = False
        if f"uses: actions/{action}@" in stripped:
            in_step = True
            continue
        match = re.match(r"^name:\s*(\S+)", stripped)
        if in_step and match is not None:
            names.append(match.group(1))
    return names


def check_release_packaging(text: str, floor: str) -> list[str]:
    """Violations of the rule that the tested files are the published files (design D5, D8)."""
    violations: list[str] = []
    package = job_text(text, "package")
    smoke = job_text(text, "smoke")
    release = job_text(text, "release")
    for name, body in (("package", package), ("smoke", smoke), ("release", release)):
        if not body:
            violations.append(f"no `{name}` job")
    if violations:
        return violations
    if "-m build" not in package or "upload-artifact@" not in package:
        violations.append("package does not build and upload dist/")
    if "fetch-depth: 0" not in package:
        violations.append("package does not check out the full history")
    if "download-artifact@" not in smoke or "verify_wheel.py --dist dist --expect-version" not in smoke:
        violations.append("smoke does not verify the downloaded wheel against the tag version")
    violations.extend(check_wheel_matrix(smoke, "smoke", floor))
    if not re.search(r"^\s*needs:\s*\[?[^\n]*\bsmoke\b", release, re.MULTILINE):
        violations.append("release does not depend on smoke")
    if not re.search(r"^\s*needs:\s*\[?[^\n]*\bchecks\b", release, re.MULTILINE):
        violations.append("release does not depend on checks")
    if "download-artifact@" not in release:
        violations.append("release does not download the tested files")
    if "-m build" in release or "pip install --upgrade build" in release:
        violations.append("release builds the files again")
    for name, body in (("package", package), ("smoke", smoke)):
        if "contents: write" in body:
            violations.append(f"{name} grants contents: write")
    uploaded = artifact_names(package, "upload-artifact")
    if len(uploaded) != 1:
        violations.append("package does not upload exactly one named artifact")
    for name, body in (("smoke", smoke), ("release", release)):
        if artifact_names(body, "download-artifact") != uploaded:
            violations.append(f"{name} does not download the artifact that package uploads")
    if not re.search(r"^\s*needs:\s*\[?[^\n]*\bpackage\b", smoke, re.MULTILINE):
        violations.append("smoke does not depend on package")
    for name, body in (("smoke", smoke), ("release", release)):
        if any("continue-on-error" in line for _, line in _code_lines(body)):
            violations.append(f"{name} sets continue-on-error")
        if any(re.match(r"^\s*(-\s+)?if:", line) for _, line in _code_lines(body)):
            violations.append(f"{name} has an `if:` condition")
    for number, line in _code_lines(text):
        if "github.ref_name" in line and not re.match(r"^\s*(TAG:|tag_name:)", line):
            violations.append(f"line {number}: the tag is used outside env or tag_name")
    return violations


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


class EvidenceGateTests(unittest.TestCase):
    """``release.yml`` gates final tags on live evidence (design D9)."""

    def release_text(self) -> str:
        return (WORKFLOWS_DIR / "release.yml").read_text(encoding="utf-8")

    def test_release_workflow_satisfies_the_gate_rules(self) -> None:
        self.assertEqual(check_evidence_gate(self.release_text()), [])

    def test_evidence_step_is_in_checks_not_in_release(self) -> None:
        text = self.release_text()
        self.assertIn("Release evidence (final tags)", job_text(text, "checks"))
        self.assertNotIn("Release evidence (final tags)", job_text(text, "release"))

    def test_checks_keeps_read_only_permissions(self) -> None:
        checks = job_text(self.release_text(), "checks")
        self.assertIn("contents: read", checks)
        self.assertNotIn("contents: write", checks)

    def test_no_job_uses_a_self_hosted_runner(self) -> None:
        self.assertEqual(check_no_self_hosted(self.release_text()), [])

    def test_the_tag_is_never_interpolated_into_a_shell_line(self) -> None:
        for number, line in enumerate(self.release_text().splitlines(), 1):
            if "verify_release_evidence.py" in line and not line.lstrip().startswith("#"):
                self.assertNotIn("${{", line, f"line {number}")
                self.assertIn('"$TAG"', line)

    def test_release_job_permissions_and_pins_are_unchanged(self) -> None:
        text = self.release_text()
        self.assertEqual(check_top_level_permissions(text), [])
        self.assertEqual(check_uses_pinned(text), [])
        self.assertEqual(len(contents_write_grants(text)), 1)

    def test_synthetic_workflow_without_the_gate_fails(self) -> None:
        text = (
            "name: Synthetic\n"
            "on:\n"
            "  push:\n"
            "jobs:\n"
            "  checks:\n"
            "    permissions:\n"
            "      contents: read\n"
            "    steps:\n"
            "      - run: echo ok\n"
            "  release:\n"
            "    permissions:\n"
            "      contents: write\n"
            "    steps:\n"
            "      - run: echo ok\n"
        )
        violations = check_evidence_gate(text)
        self.assertGreaterEqual(len(violations), 4)

    def test_tag_interpolated_inline_is_flagged(self) -> None:
        text = self.release_text().replace(
            'python3 scripts/verify_release_evidence.py --tag "$TAG"\n',
            "python3 scripts/verify_release_evidence.py --tag ${{ github.ref_name }}\n",
            1,
        )
        self.assertTrue(
            any("interpolates" in violation for violation in check_evidence_gate(text))
        )


class WheelPackagingTests(unittest.TestCase):
    """CI builds the wheel and release publishes the files it tested (design D8)."""

    def ci_text(self) -> str:
        return (WORKFLOWS_DIR / "ci.yml").read_text(encoding="utf-8")

    def release_text(self) -> str:
        return (WORKFLOWS_DIR / "release.yml").read_text(encoding="utf-8")

    def test_the_floor_comes_from_pyproject(self) -> None:
        self.assertRegex(python_floor(), r"^\d+\.\d+$")

    def test_ci_wheel_job_satisfies_the_rules(self) -> None:
        self.assertEqual(check_wheel_job(self.ci_text(), "wheel", python_floor()), [])

    def test_ci_wheel_job_builds_with_full_history_and_checks_out_read_only(self) -> None:
        job = job_text(self.ci_text(), "wheel")
        self.assertIn("fetch-depth: 0", job)
        self.assertIn("python3 -m build", job)
        self.assertNotIn("permissions:", job)  # inherits the read-only top level

    def test_release_workflow_tests_the_files_it_publishes(self) -> None:
        self.assertEqual(check_release_packaging(self.release_text(), python_floor()), [])

    def test_release_job_keeps_the_only_contents_write(self) -> None:
        text = self.release_text()
        self.assertEqual(contents_write_grants(text), ["release"])
        self.assertEqual(check_evidence_gate(text), [])

    def test_smoke_passes_the_tag_through_env(self) -> None:
        smoke = job_text(self.release_text(), "smoke")
        self.assertIn("TAG: ${{ github.ref_name }}", smoke)
        self.assertIn('--expect-version "${TAG#v}"', smoke)

    def test_a_release_job_that_builds_again_is_flagged(self) -> None:
        text = self.release_text().replace(
            "      - name: Download the tested files\n",
            "      - name: Build\n        run: python3 -m build\n      - name: Download the tested files\n",
            1,
        )
        self.assertIn("release builds the files again", check_release_packaging(text, python_floor()))

    def test_a_release_without_smoke_is_flagged(self) -> None:
        text = self.release_text().replace("needs: [checks, smoke]", "needs: [checks]", 1)
        self.assertIn("release does not depend on smoke", check_release_packaging(text, python_floor()))

    def test_a_renamed_artifact_in_one_job_is_flagged(self) -> None:
        text = self.release_text()
        download = "      - name: Download the tested files\n        uses: actions/download-artifact@"
        head, tail = text.split(download, 1)
        renamed = head + download + tail.replace("name: dist", "name: other", 1)
        self.assertIn(
            "release does not download the artifact that package uploads",
            check_release_packaging(renamed, python_floor()),
        )
        renamed = text.replace("          name: dist\n          path: dist/", "          name: other\n          path: dist/", 1)
        violations = check_release_packaging(renamed, python_floor())
        self.assertIn("smoke does not download the artifact that package uploads", violations)
        self.assertIn("release does not download the artifact that package uploads", violations)

    def test_a_smoke_without_needs_package_is_flagged(self) -> None:
        text = self.release_text().replace("    needs: package\n", "", 1)
        self.assertIn("smoke does not depend on package", check_release_packaging(text, python_floor()))

    def test_continue_on_error_in_smoke_is_flagged(self) -> None:
        text = self.release_text().replace(
            "    needs: package\n", "    needs: package\n    continue-on-error: true\n", 1
        )
        self.assertIn("smoke sets continue-on-error", check_release_packaging(text, python_floor()))

    def test_continue_on_error_in_release_is_flagged(self) -> None:
        text = self.release_text().replace(
            "    needs: [checks, smoke]\n", "    needs: [checks, smoke]\n    continue-on-error: true\n", 1
        )
        self.assertIn("release sets continue-on-error", check_release_packaging(text, python_floor()))

    def test_an_if_condition_on_release_is_flagged(self) -> None:
        text = self.release_text().replace(
            "    needs: [checks, smoke]\n", "    needs: [checks, smoke]\n    if: always()\n", 1
        )
        self.assertIn("release has an `if:` condition", check_release_packaging(text, python_floor()))

    def test_an_if_condition_on_smoke_is_flagged(self) -> None:
        text = self.release_text().replace(
            "    needs: package\n", "    needs: package\n    if: false\n", 1
        )
        self.assertIn("smoke has an `if:` condition", check_release_packaging(text, python_floor()))

    def test_the_tag_in_a_shell_line_is_flagged(self) -> None:
        text = self.release_text().replace('"${TAG#v}"', "${{ github.ref_name }}", 1)
        violations = check_release_packaging(text, python_floor())
        self.assertTrue(any("outside env" in violation for violation in violations))

    def test_the_wheel_matrix_has_the_linux_and_macos_legs(self) -> None:
        legs = matrix_legs(job_text(self.ci_text(), "wheel"))
        self.assertEqual(
            legs,
            [
                {"python": python_floor(), "os": "ubuntu-24.04"},
                {"python": "3.x", "os": "ubuntu-latest"},
                {"python": "3.x", "os": "macos-latest"},
            ],
        )

    def test_the_release_smoke_matrix_has_the_macos_leg(self) -> None:
        legs = matrix_legs(job_text(self.release_text(), "smoke"))
        self.assertIn({"python": "3.x", "os": "macos-latest"}, legs)
        self.assertEqual(check_release_packaging(self.release_text(), python_floor()), [])

    def test_a_missing_macos_leg_is_flagged_in_both_jobs(self) -> None:
        leg = '          - python: "3.x"\n            os: macos-latest\n'
        for text, job, check in (
            (self.ci_text(), "wheel", lambda t: check_wheel_job(t, "wheel", python_floor())),
            (self.release_text(), "smoke", lambda t: check_release_packaging(t, python_floor())),
        ):
            with self.subTest(job=job):
                self.assertIn(leg, text)
                self.assertIn(
                    f"{job} 3.x legs do not run on ubuntu-latest and macos-latest",
                    check(text.replace(leg, "", 1)),
                )

    def test_the_macos_leg_runs_no_live_tier_and_is_not_skipped(self) -> None:
        for text, job in ((self.ci_text(), "wheel"), (self.release_text(), "smoke")):
            with self.subTest(job=job):
                body = job_text(text, job)
                self.assertNotIn("self-hosted", body)
                self.assertNotIn("SHIM_TIERS", body)
                self.assertNotIn("continue-on-error", body)

    def test_the_smoke_legs_equal_the_wheel_legs(self) -> None:
        self.assertEqual(check_legs_match(self.ci_text(), self.release_text()), [])

    def test_smoke_legs_differing_from_the_wheel_legs_are_flagged(self) -> None:
        text = self.release_text().replace("os: ubuntu-24.04", "os: ubuntu-22.04", 1)
        violations = check_legs_match(self.ci_text(), text)
        self.assertTrue(violations)

    def test_a_smoke_floor_leg_on_a_latest_image_is_flagged(self) -> None:
        text = self.release_text().replace("os: ubuntu-24.04", "os: ubuntu-latest", 1)
        self.assertIn(
            "smoke floor leg is not pinned to a versioned image",
            check_release_packaging(text, python_floor()),
        )

    def test_a_missing_python_floor_is_flagged(self) -> None:
        text = self.ci_text().replace(
            '          - python: "3.9"\n            os: ubuntu-24.04\n', "", 1
        )
        self.assertIn(
            f"wheel does not run on the floor {python_floor()} and 3.x",
            check_wheel_job(text, "wheel", python_floor()),
        )

    def test_a_floor_leg_on_a_latest_image_is_flagged(self) -> None:
        text = self.ci_text().replace("os: ubuntu-24.04", "os: ubuntu-latest", 1)
        self.assertIn(
            "wheel floor leg is not pinned to a versioned image",
            check_wheel_job(text, "wheel", python_floor()),
        )

    def test_a_literal_runs_on_is_flagged(self) -> None:
        job = job_text(self.ci_text(), "wheel")
        text = self.ci_text().replace(
            "runs-on: ${{ matrix.os }}", "runs-on: ubuntu-latest", 1
        )
        self.assertIn("runs-on: ${{ matrix.os }}", job)
        self.assertIn(
            "wheel does not select the runner from the matrix",
            check_wheel_job(text, "wheel", python_floor()),
        )

    def test_a_3x_leg_on_a_versioned_image_is_flagged(self) -> None:
        text = self.ci_text().replace("os: ubuntu-latest", "os: ubuntu-24.04", 1)
        self.assertIn(
            "wheel 3.x legs do not run on ubuntu-latest and macos-latest",
            check_wheel_job(text, "wheel", python_floor()),
        )

    def test_the_matrix_in_a_run_line_is_flagged(self) -> None:
        text = self.ci_text().replace(
            "run: python3 scripts/verify_wheel.py --dist dist",
            "run: python3 scripts/verify_wheel.py --dist dist --os ${{ matrix.os }}",
            1,
        )
        self.assertIn(
            "wheel interpolates the matrix into a run line",
            check_wheel_job(text, "wheel", python_floor()),
        )

    def test_a_write_permission_in_the_wheel_job_is_flagged(self) -> None:
        text = self.ci_text().replace(
            "  wheel:\n", "  wheel:\n    permissions:\n      contents: write\n", 1
        )
        self.assertIn("wheel grants contents: write", check_wheel_job(text, "wheel", python_floor()))


class ConformanceRequireTests(unittest.TestCase):
    """The conformance job of ``ci.yml`` requires every test to run (design D9).

    ``--require conformance`` makes the report step fail (exit 3) when any
    conformance test is not run, skipped, or missing, so a runner without
    Docker cannot pass with zero executed tests.
    """

    def ci_text(self) -> str:
        return (WORKFLOWS_DIR / "ci.yml").read_text(encoding="utf-8")

    def test_conformance_job_passes_require_conformance(self) -> None:
        job = job_text(self.ci_text(), "conformance")
        self.assertIn("--require conformance", job)

    def test_conformance_job_keeps_if_always(self) -> None:
        job = job_text(self.ci_text(), "conformance")
        self.assertIn("if: always()", job)

    def test_workflow_hard_codes_no_test_count(self) -> None:
        text = self.ci_text()
        job = job_text(text, "conformance")
        self.assertIsNone(re.search(r"--(expect|count)\b", text))
        self.assertIsNone(re.search(r"--(expect|count)\b", job))
        self.assertEqual(check_conformance_require(text), [])

    def test_synthetic_workflow_without_the_flag_fails(self) -> None:
        text = (
            "name: Synthetic\n"
            "on:\n"
            "  push:\n"
            "permissions:\n"
            "  contents: read\n"
            "jobs:\n"
            "  conformance:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Conformance test report\n"
            "        run: python3 scripts/test_report.py\n"
            "      - name: Record versions\n"
            "        if: always()\n"
            "        run: echo ok\n"
        )
        violations = check_conformance_require(text)
        self.assertTrue(violations)
        self.assertTrue(
            any("--require conformance" in violation for violation in violations)
        )


if __name__ == "__main__":
    unittest.main()
