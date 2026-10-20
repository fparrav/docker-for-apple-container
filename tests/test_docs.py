from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim.cli import SUPPORTED_CONTAINER_VERSION, print_help  # noqa: E402

TIER = "unit"


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
