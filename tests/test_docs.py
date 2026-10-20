from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim.cli import SUPPORTED_CONTAINER_VERSION, print_help  # noqa: E402

TIER = "unit"


class DocumentationConsistencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.readme = (ROOT / "README.md").read_text()

    def test_runtime_compatibility_baseline_is_consistent(self) -> None:
        documents = {"README.md": self.readme}
        for name, document in documents.items():
            with self.subTest(document=name):
                self.assertIn(SUPPORTED_CONTAINER_VERSION, document)
                self.assertIn("no backward compatibility", document.lower())
                self.assertNotRegex(document, r"1\.[12]\.\d+|1\.3\.1 (?:or newer|or later|\+)")

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


if __name__ == "__main__":
    unittest.main()
