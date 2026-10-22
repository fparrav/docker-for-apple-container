from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim.cli import SUPPORTED_CONTAINER_VERSION, print_help  # noqa: E402

TIER = "unit"


PACKAGE = "docker-for-apple-container-fork"
GIT_URL = "git+https://github.com/fparrav/docker-for-apple-container"

# `uv tool install` takes the source with --from and the distribution as the
# positional argument, or one PEP 508 direct reference. `--from <package>`
# followed by a URL is rejected by uv ("conflicts with install request").
UV_FROM_FORM = re.compile(
    r"^uv tool install --from " + re.escape(GIT_URL) + r"@\S+ " + re.escape(PACKAGE) + r"$"
)
UV_REFERENCE_FORM = re.compile(
    r'^uv tool install "' + re.escape(PACKAGE) + " @ " + re.escape(GIT_URL) + r'@[^"\s]+"$'
)


def uv_release_commands(text: str) -> list:
    """The `uv tool install` commands that install a tagged release from git."""
    joined = re.sub(r"\\\n\s*", " ", text)
    return [
        " ".join(line.split())
        for line in joined.splitlines()
        if line.strip().startswith("uv tool install") and "git+" in line
    ]


def is_valid_uv_release_command(command: str) -> bool:
    return bool(UV_FROM_FORM.match(command) or UV_REFERENCE_FORM.match(command))


# D7: a release literal is a dotted x.y.z version preceded by "container",
# "container CLI", or "Apple container". The captured group is the version.
RELEASE_LITERAL = re.compile(r"\b(?:Apple\s+)?container(?:\s+CLI)?\s+(\d+\.\d+\.\d+)")


def find_release_literals(text: str) -> list:
    """Return every release literal (x.y.z) in *text*, in order.

    Operates on in-memory text so it can be unit tested without the
    filesystem. A document that refers to the ``SUPPORTED_CONTAINER_VERSION``
    constant instead of a literal yields an empty list and therefore passes.
    Backticks (Markdown inline code) are stripped before matching so a literal
    wrapped in code, e.g. ``Apple `container` 1.5.0``, is still detected.
    """
    return RELEASE_LITERAL.findall(text.replace("`", ""))


def stale_release_literals(text: str, supported: str) -> list:
    """Return the release literals in *text* that differ from *supported*."""
    return [literal for literal in find_release_literals(text) if literal != supported]


def required_documents(root: Path) -> list:
    """Documents the consistency test scans, in a stable order.

    README.md and every docs/*.md, plus AGENTS.md and CLAUDE.md. Absent files
    are reported as not checked rather than passing silently.
    """
    docs = [root / "README.md"]
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        docs.extend(sorted(docs_dir.glob("*.md")))
    docs.append(root / "AGENTS.md")
    docs.append(root / "CLAUDE.md")
    return docs


def scan_document(name: str, text, supported: str) -> tuple:
    """Scan one document's in-memory text. Returns ``(stale_findings, not_checked)``.

    ``text`` is the document body, or ``None`` when the file is absent. A
    missing document is reported in ``not_checked`` rather than passing
    silently; a present document contributes ``(name, literal)`` findings for
    each release literal that differs from *supported*. Operating on
    ``(name, text)`` keeps the scanner filesystem-free for unit tests.
    """
    if text is None:
        return [], [name]
    stale = [(name, literal) for literal in stale_release_literals(text, supported)]
    return stale, []


def scan_documents(paths, supported: str, root: Path) -> tuple:
    """Scan document files. Returns ``(stale_findings, not_checked)``.

    Documents are named by their path relative to *root* (e.g. ``docs/foo.md``)
    so a finding identifies the exact file. Reads each present file with
    ``Path.read_text`` and delegates to :func:`scan_document`; absent files are
    passed as ``None`` and reported as not checked.
    """
    stale_findings = []
    not_checked = []
    for path in paths:
        name = path.relative_to(root).as_posix()
        text = path.read_text() if path.is_file() else None
        stale, not_checked_docs = scan_document(name, text, supported)
        stale_findings.extend(stale)
        not_checked.extend(not_checked_docs)
    return stale_findings, not_checked


class DocumentationConsistencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.readme = (ROOT / "README.md").read_text()

    def test_documents_do_not_state_other_release(self) -> None:
        documents = required_documents(ROOT)
        stale_findings, not_checked = scan_documents(documents, SUPPORTED_CONTAINER_VERSION, ROOT)
        self.assertEqual(
            stale_findings,
            [],
            "documents state release literals other than "
            f"{SUPPORTED_CONTAINER_VERSION}: {stale_findings}",
        )
        # Surface absent documents after validating stale, rather than passing silently.
        for path in documents:
            name = path.relative_to(ROOT).as_posix()
            with self.subTest(document=name):
                if name in not_checked:
                    self.skipTest(f"{name}: not checked (absent)")

    def test_runtime_compatibility_baseline_is_consistent(self) -> None:
        documents = {"README.md": self.readme}
        for name, document in documents.items():
            with self.subTest(document=name):
                self.assertIn(SUPPORTED_CONTAINER_VERSION, document)
                self.assertIn("no backward compatibility", document.lower())
                self.assertNotRegex(document, r"1\.[12]\.\d+|1\.3\.1 (?:or newer|or later|\+)")

    def test_readme_has_no_upstream_install_source_or_badge(self) -> None:
        for block in re.findall(r"```[^\n]*\n(.*?)```", self.readme, re.DOTALL):
            self.assertNotIn("appautomaton", block)
        for line in self.readme.splitlines():
            if "img.shields.io" in line:
                self.assertNotIn("appautomaton", line)

    def test_readme_fork_notice_links_upstream(self) -> None:
        self.assertIn("Fork of", self.readme)
        self.assertIn(
            "https://github.com/appautomaton/docker-for-apple-container",
            self.readme,
        )

    def test_readme_install_commands_use_the_fork_repository(self) -> None:
        install = self.readme.split("## Install", 1)[1].split(
            "## Requirements", 1
        )[0]
        self.assertGreaterEqual(
            install.count("git+https://github.com/fparrav/docker-for-apple-container"),
            2,
        )
        self.assertIn("uv tool install .", install)

    def test_uv_release_command_is_valid_and_the_same_in_every_document(self) -> None:
        commands = {}
        for name in ("README.md", "docs/releasing.md"):
            found = uv_release_commands((ROOT / name).read_text())
            self.assertEqual(len(found), 1, f"{name} must document exactly one uv release command")
            self.assertTrue(
                is_valid_uv_release_command(found[0]),
                f"{name} documents a uv command uv rejects: {found[0]}",
            )
            commands[name] = found[0]
        self.assertEqual(commands["README.md"], commands["docs/releasing.md"])

    def test_uv_command_validator_rejects_the_inverted_form(self) -> None:
        inverted = f"uv tool install --from {PACKAGE} {GIT_URL}@v1.2.3"
        valid_from = f"uv tool install --from {GIT_URL}@v1.2.3 {PACKAGE}"
        valid_reference = f'uv tool install "{PACKAGE} @ {GIT_URL}@v1.2.3"'
        self.assertFalse(is_valid_uv_release_command(inverted))
        self.assertTrue(is_valid_uv_release_command(valid_from))
        self.assertTrue(is_valid_uv_release_command(valid_reference))
        text = f"```bash\nuv tool install --from {PACKAGE} \\\n    {GIT_URL}@v1.2.3\n```\n"
        self.assertEqual(uv_release_commands(text), [inverted])

    def test_uv_pipx_command_is_unchanged(self) -> None:
        for name in ("README.md", "docs/releasing.md"):
            self.assertIn(f"pipx install {GIT_URL}@vX.Y.Z", (ROOT / name).read_text())

    @unittest.skipUnless(
        os.environ.get("SHIM_TEST_UV") == "1" and shutil.which("uv"),
        "set SHIM_TEST_UV=1 with uv on PATH to run the install check",
    )
    def test_uv_from_form_installs_the_docker_executable(self) -> None:
        # Same shape as the documented command, with the source tree as the
        # source; both tool directories are disposable.
        with tempfile.TemporaryDirectory(prefix="shimtest-uv-") as tmp:
            env = dict(os.environ, UV_TOOL_DIR=f"{tmp}/tools", UV_TOOL_BIN_DIR=f"{tmp}/bin")
            result = subprocess.run(
                ["uv", "tool", "install", "--from", str(ROOT), PACKAGE],
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(os.path.exists(f"{tmp}/bin/docker"))

    def test_readme_states_the_supported_release_once(self) -> None:
        requirements = self.readme.split("## Requirements", 1)[1].split(
            "## Supported Docker Subset", 1
        )[0]
        self.assertEqual(requirements.count(SUPPORTED_CONTAINER_VERSION), 1)

    def test_readme_passthrough_does_not_claim_untranslated_commands(self) -> None:
        passthrough = self.readme.split("### Thin passthrough", 1)[1].split(
            "### Compose", 1
        )[0]
        self.assertNotIn(
            "`pull`/`rm`/`tag`/`push`/`save`/`load`/`prune`/`ls`",
            passthrough,
        )

    def test_readme_documents_the_compose_normalizer(self) -> None:
        for expected in (
            "brew install docker-compose",
            "CONTAINER_DOCKER_SHIM_COMPOSE",
            "normalizer: official",
            "normalizer: built-in subset",
            "5.6.0",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.readme)
        self.assertNotIn("Anchors, multi-document streams, and `|`/`>` block scalars are\n  out of scope", self.readme)

    def test_compose_ps_format_claims_match_matrix(self) -> None:
        entries = json.loads((ROOT / "tests/compat/matrix.json").read_text())
        formats = {
            entry["id"]: entry["status"] for entry in entries
            if entry["kind"] == "format" and entry["id"].startswith("docker compose ps")
        }
        supported = [key for key, status in formats.items() if status == "SUPPORTED"]
        refused = [key for key, status in formats.items() if status == "UNSUPPORTED"]
        self.assertEqual(set(supported), {
            "docker compose ps --format json", "docker compose ps --format table"
        })
        self.assertEqual(len(refused), 2)
        paragraph = self.readme.split("### Behavior for scripts and agents", 1)[1].split(
            "### Security options", 1
        )[0]
        self.assertIn("docker compose ps --format json|table", paragraph)
        self.assertIn("refuses Go templates", paragraph)
        self.assertIn("formats other than `json` or", paragraph)

    def test_run_cpus_mount_volume_notes_state_the_verified_semantics(self) -> None:
        entries = json.loads((ROOT / "tests/compat/matrix.json").read_text())
        by_id = {entry["id"]: entry for entry in entries}

        cpus = by_id["run --cpus"]["note"].lower()
        self.assertIn("integer", cpus)
        self.assertIn("fractional", cpus)
        self.assertIn("refus", cpus)

        mount = by_id["run --mount"]["note"].lower()
        self.assertIn("translated", mount)
        self.assertNotIn("without content validation", mount)

        volume = by_id["run --volume"]["note"]
        self.assertIn("z/Z", volume)
        self.assertIn("refus", volume.lower())
        self.assertIn("readonly", volume)

    def test_readme_compat_block_carries_the_run_mount_semantics(self) -> None:
        entries = json.loads((ROOT / "tests/compat/matrix.json").read_text())
        by_id = {entry["id"]: entry for entry in entries}
        block = self.readme.split("<!-- compat-matrix:start -->", 1)[1].split(
            "<!-- compat-matrix:end -->", 1
        )[0]
        for entry_id in ("run --cpus", "run --mount", "run --volume"):
            with self.subTest(entry=entry_id):
                self.assertIn(by_id[entry_id]["note"], block)

    def test_cli_help_uses_the_same_image_classification(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            print_help()
        help_text = output.getvalue()
        self.assertIn(f"Supported runtime: Apple container {SUPPORTED_CONTAINER_VERSION} only", help_text)
        self.assertIn("images, image inspect", help_text)
        self.assertNotIn("image <sub>", help_text)
        self.assertIn(
            "container inspect, container port, container prune",
            help_text,
        )


class VolumeLifecycleDocsTests(unittest.TestCase):
    """The README and matrix describe the anonymous volume lifecycle and ``rbind`` refusal."""

    def readme(self) -> str:
        return (ROOT / "README.md").read_text(encoding="utf-8")

    def test_readme_describes_orphans_renewal_and_down_v(self) -> None:
        text = " ".join(self.readme().split())
        self.assertIn("The shim never deletes an anonymous volume on its own", text)
        self.assertIn("`up -V` (`--renew-anon-volumes`)", text)
        self.assertIn("orphan", text)
        self.assertIn("`down -v` removes every volume that carries the project and anonymous labels", text)
        self.assertIn("docker volume ls --filter label=com.docker.compose.project=<project>", text)

    def test_readme_no_longer_says_up_discards_or_reseeds_leftover_volumes(self) -> None:
        text = " ".join(self.readme().split())
        self.assertNotIn("**Breaking change:** data that used to survive", text)
        self.assertNotIn("the next `up` that creates the container discards it", text)
        self.assertNotIn("discards and seeds again", text)
        matrix = (ROOT / "tests" / "compat" / "matrix.json").read_text(encoding="utf-8")
        self.assertNotIn("discards and seeds again", matrix)
        self.assertNotIn("discarded and seeded again", matrix)

    def test_no_document_says_the_shim_keeps_anonymous_volumes_across_down(self) -> None:
        for name in ("README.md", "docs/testing.md", "docs/releasing.md"):
            text = " ".join((ROOT / name).read_text(encoding="utf-8").split())
            self.assertNotIn("unlike Docker, which drops them", text, name)
            self.assertNotIn("the shim keeps them", text, name)
            self.assertNotIn("data survives recreating the container", text, name)

    def test_rbind_is_listed_as_refused_not_accepted(self) -> None:
        text = " ".join(self.readme().split())
        self.assertIn("`o: bind` (optionally with `ro` or `rw`)", text)
        self.assertNotIn("optionally `rbind`", text)
        self.assertIn("`o: rbind` is refused with exit 64", text)
        matrix = (ROOT / "tests" / "compat" / "matrix.json").read_text(encoding="utf-8")
        self.assertNotIn("o of bind, rbind", matrix)
        self.assertIn("o: rbind is refused", matrix)


class ReleaseEvidenceDocsTests(unittest.TestCase):
    """``docs/releasing.md`` documents the evidence procedure and its limits."""

    def releasing(self) -> str:
        return (ROOT / "docs" / "releasing.md").read_text(encoding="utf-8")

    def test_releasing_documents_the_evidence_procedure(self) -> None:
        text = self.releasing()
        for needle in (
            "validate-runtime.py --evidence",
            "docs/evidence/",
            "verify_release_evidence.py",
            "30 days",
            "Pre-release tags",
            "pre-release",
        ):
            self.assertIn(needle, text, needle)

    def test_releasing_states_the_limits_of_the_evidence(self) -> None:
        text = self.releasing()
        self.assertIn("not signed", text)
        self.assertIn("attestation", text)

    def test_releasing_lists_the_recognised_tag_forms(self) -> None:
        text = self.releasing()
        for tag in ("vX.Y.Z", "vX.Y.Zrc1", "vX.Y.Za1", "vX.Y.Zb1", "vX.Y.Z.dev1"):
            self.assertIn(tag, text, tag)
        self.assertNotIn("v0.1.0-rc1` are fine", text)

    def test_testing_documents_evidence_and_require(self) -> None:
        text = (ROOT / "docs" / "testing.md").read_text(encoding="utf-8")
        self.assertIn("--evidence", text)
        self.assertIn("--require", text)
        self.assertIn("releasing.md#release-evidence", text)

    def test_releasing_documents_the_wheel_check_and_the_evidence_inputs(self) -> None:
        text = self.releasing()
        text = " ".join(text.split())
        for needle in (
            "verify_wheel.py",
            "`package`",
            "`smoke`",
            "files that were installed and tested are the files",
            "`pyproject.toml`",
            "schema 2",
        ):
            self.assertIn(needle, text, needle)
        self.assertNotIn("`src/`, `scripts/` and `tests/`", text)

    def test_testing_documents_waiting_and_the_wheel_check(self) -> None:
        text = (ROOT / "docs" / "testing.md").read_text(encoding="utf-8")
        for needle in ("wait_until", "tests/waiting.py", "pg_isready -h 127.0.0.1", "verify_wheel.py"):
            self.assertIn(needle, text, needle)
        self.assertIn("`pyproject.toml`", text)


def workflow_floor_legs(name: str) -> list:
    """The ``(python, os)`` pairs of ``.github/workflows/<name>`` whose python is not ``3.x``."""
    text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
    legs = re.findall(r'-\s*python:\s*"([^"]+)"\s*\n\s*os:\s*(\S+)', text)
    return [leg for leg in legs if leg[0] != "3.x"]


class WheelLegsDocsTests(unittest.TestCase):
    """The documented wheel check legs and evidence consequence match the workflows."""

    def normalised(self, name: str) -> str:
        return " ".join((ROOT / "docs" / name).read_text(encoding="utf-8").split())

    def test_documented_floor_leg_equals_the_workflows(self) -> None:
        legs = workflow_floor_legs("ci.yml")
        self.assertEqual(len(legs), 1, legs)
        self.assertEqual(workflow_floor_legs("release.yml"), legs)
        python, runner = legs[0]
        text = self.normalised("releasing.md")
        self.assertIn(f"| `{python}` | `{runner}` |", text)
        self.assertIn("| `3.x` | `ubuntu-latest` |", text)
        self.assertIn(f"`{runner}` is the last hosted image", text)

    def test_documents_say_scripts_and_tests_invalidate_the_evidence(self) -> None:
        expected = {
            "releasing.md": "`validate-runtime.py --evidence` must run again",
            "testing.md": "invalidates the recorded live evidence",
        }
        for name, consequence in expected.items():
            with self.subTest(document=name):
                text = self.normalised(name)
                self.assertIn("change to `scripts/` or `tests/`", text)
                self.assertIn(consequence, text)

    def test_releasing_documents_the_pin_maintenance_and_environment_line(self) -> None:
        text = self.normalised("releasing.md")
        for needle in (
            "versions-manifest.json",
            "platform_version",
            "ok environment: Python <version> on <platform>",
            "The previous file is not edited or deleted",
        ):
            self.assertIn(needle, text, needle)

    def test_no_document_calls_the_wheel_check_live_evidence(self) -> None:
        releasing = self.normalised("releasing.md")
        testing = self.normalised("testing.md")
        self.assertIn("are not live evidence", releasing)
        self.assertIn("not live evidence", testing)
        for text in (releasing, testing):
            self.assertNotRegex(text, r"wheel check (?:is|counts as|provides) live evidence")

    def test_testing_documents_the_dist_forms(self) -> None:
        text = self.normalised("testing.md")
        self.assertIn("`--dist` may be a relative or an absolute path", text)
        self.assertIn("CI runs it as `--dist dist`", text)


class ReleaseLiteralScannerTests(unittest.TestCase):
    def test_scanner_flags_stale_literal(self) -> None:
        stale, not_checked = scan_document(
            "x.md", "Verified against Apple container 1.4.1 on macOS 26.", "1.5.0"
        )
        self.assertEqual(stale, [("x.md", "1.4.1")])
        self.assertEqual(not_checked, [])

    def test_scanner_passes_current_release(self) -> None:
        stale, not_checked = scan_document("x.md", "Apple container 1.5.0", "1.5.0")
        self.assertEqual(stale, [])
        self.assertEqual(not_checked, [])

    def test_scanner_passes_constant_reference(self) -> None:
        text = "the one declared by `SUPPORTED_CONTAINER_VERSION` in cli.py"
        self.assertEqual(find_release_literals(text), [])
        stale, not_checked = scan_document("x.md", text, SUPPORTED_CONTAINER_VERSION)
        self.assertEqual(stale, [])
        self.assertEqual(not_checked, [])

    def test_scanner_matches_all_prefix_forms(self) -> None:
        self.assertEqual(find_release_literals("container 1.2.3"), ["1.2.3"])
        self.assertEqual(find_release_literals("container CLI 1.2.3"), ["1.2.3"])
        self.assertEqual(find_release_literals("Apple container 1.2.3"), ["1.2.3"])

    def test_scanner_detects_backtick_wrapped_literal(self) -> None:
        text = "macOS 26 with Apple `container` 1.4.1 only"
        self.assertEqual(find_release_literals(text), ["1.4.1"])
        stale, not_checked = scan_document("x.md", text, "1.5.0")
        self.assertEqual(stale, [("x.md", "1.4.1")])
        self.assertEqual(not_checked, [])

    def test_scanner_reports_missing_document_not_checked(self) -> None:
        stale, not_checked = scan_document("missing.md", None, "1.5.0")
        self.assertEqual(stale, [])
        self.assertEqual(not_checked, ["missing.md"])


if __name__ == "__main__":
    unittest.main()
