"""Unit test: every conformance fixture is used by a conformance test.

Implements design D7 of the ``close-lifecycle-and-evidence-gaps`` change: a
non-opt-in unit test over fixture *names*, not a run. It reads the first line
of each fixture's ``compose.yaml`` under ``tests/fixtures/compat/``, keeps the
directories whose ``# tiers: ...`` header lists ``conformance``, and fails
naming any such fixture that no conformance test uses. Fixtures without that
header (``ps-reference`` has no compose file at all) are not required to have
a conformance test.

A fixture is *used* when a test function of ``tests/test_conformance_fixtures.py``
mentions its name, either as a string literal or through a ``FIXTURE_*``
constant. The constants are imported from the live-tier modules, so their
values are resolved from the module-level assignments there. Merely importing
a constant does not count, and a fixture that only a live test uses is
reported.
"""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TIER = "unit"

FIXTURES_DIR = ROOT / "tests" / "fixtures" / "compat"

CONFORMANCE_MODULE = "tests/test_conformance_fixtures.py"

# Modules that define the FIXTURE_* constants the conformance module imports.
CONSTANT_MODULES = (
    "tests/test_live_fixtures.py",
    "tests/test_live_images.py",
)


def _declared_tiers(first_line: str) -> list[str]:
    """Tiers named by a ``# tiers: a, b`` header line.

    Returns an empty list when the line is not a ``# tiers:`` comment (case-
    insensitive on the key, whitespace-tolerant around the colon).
    """
    line = first_line.strip()
    if not line.startswith("#"):
        return []
    body = line[1:].strip()
    if not body.lower().startswith("tiers:"):
        return []
    value = body[len("tiers:"):].strip()
    return [tier.strip().lower() for tier in value.split(",") if tier.strip()]


def conformance_fixtures(fixtures_dir: Path) -> list[str]:
    """Names of the fixture directories that require a conformance test.

    A directory under ``fixtures_dir`` is required only when its
    ``compose.yaml`` (or ``compose.yml``) exists and its FIRST line is a
    ``# tiers: ...`` comment listing ``conformance`` among the comma-
    separated tiers. Directories without a compose file, or without such a
    header, are not required. Sorted by directory name.
    """
    fixtures_dir = Path(fixtures_dir)
    if not fixtures_dir.is_dir():
        return []
    names = []
    for entry in sorted(fixtures_dir.iterdir()):
        if not entry.is_dir():
            continue
        compose = None
        for candidate in ("compose.yaml", "compose.yml"):
            path = entry / candidate
            if path.is_file():
                compose = path
                break
        if compose is None:
            continue
        with compose.open(encoding="utf-8") as handle:
            first_line = handle.readline()
        if "conformance" in _declared_tiers(first_line):
            names.append(entry.name)
    return names


def fixture_constants(sources: list[str]) -> dict[str, str]:
    """``{FIXTURE_X: "name"}`` from the module-level assignments in ``sources``."""
    constants = {}
    for source in sources:
        for node in ast.parse(source).body:
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.startswith("FIXTURE_"):
                        constants[target.id] = node.value.value
    return constants


def used_fixtures(test_source: str, constants: dict[str, str]) -> set[str]:
    """Fixture names that a test function of ``test_source`` mentions.

    Only function bodies count (an import of a constant is not a use); a name
    is mentioned as a string literal or as a ``FIXTURE_*`` constant whose value
    is in ``constants``.
    """
    used = set()
    for function in ast.walk(ast.parse(test_source)):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Name) and node.id in constants:
                used.add(constants[node.id])
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                used.add(node.value)
    return used


def uncovered_fixtures(names: list[str], test_source: str, constants: dict[str, str]) -> list[str]:
    """Fixture names no test function of ``test_source`` uses, in the order of ``names``."""
    used = used_fixtures(test_source, constants)
    return [name for name in names if name not in used]


class FixtureCoverageTests(unittest.TestCase):
    def test_every_conformance_fixture_is_used_by_a_conformance_test(self) -> None:
        """On the real repository, every conformance headered fixture is used."""
        names = conformance_fixtures(FIXTURES_DIR)
        self.assertTrue(names, "expected conformance fixtures under %s" % FIXTURES_DIR)
        constants = fixture_constants(
            [(ROOT / rel).read_text(encoding="utf-8") for rel in CONSTANT_MODULES]
        )
        source = (ROOT / CONFORMANCE_MODULE).read_text(encoding="utf-8")
        self.assertEqual(uncovered_fixtures(names, source, constants), [])

    def test_only_conformance_headered_fixtures_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixtures_dir = Path(tmp)
            for name, first_line in (
                ("live-conformance", "# tiers: live, conformance\n"),
                ("live-only", "# tiers: live\n"),
                ("no-header", "services:\n"),
            ):
                fixture = fixtures_dir / name
                fixture.mkdir()
                (fixture / "compose.yaml").write_text(
                    first_line + "  web:\n    image: nginx\n", encoding="utf-8"
                )
            self.assertEqual(conformance_fixtures(fixtures_dir), ["live-conformance"])

    def test_uncovered_fixture_is_reported_by_name(self) -> None:
        constants = {"FIXTURE_A": "alpha-fixture", "FIXTURE_G": "gamma-fixture"}
        source = (
            "from live import FIXTURE_A, FIXTURE_G\n"
            "def test_one(self):\n"
            "    harness = FixtureHarness(self, FIXTURE_A)\n"
            "def test_two(self):\n"
            "    other = FixtureHarness(self, 'beta-fixture')\n"
        )
        self.assertEqual(
            uncovered_fixtures(
                ["alpha-fixture", "beta-fixture", "gamma-fixture"], source, constants
            ),
            ["gamma-fixture"],
        )

    def test_importing_a_constant_is_not_a_use(self) -> None:
        constants = {"FIXTURE_A": "alpha-fixture"}
        source = "from live import FIXTURE_A\n\ndef helper():\n    return 1\n"
        self.assertEqual(
            uncovered_fixtures(["alpha-fixture"], source, constants), ["alpha-fixture"]
        )

    def test_constants_are_read_from_module_level_assignments(self) -> None:
        source = 'FIXTURE_A = "alpha"\nOTHER = "x"\ndef f():\n    FIXTURE_B = "no"\n'
        self.assertEqual(fixture_constants([source]), {"FIXTURE_A": "alpha"})

    def test_ps_reference_is_not_required(self) -> None:
        self.assertNotIn("ps-reference", conformance_fixtures(FIXTURES_DIR))


if __name__ == "__main__":
    unittest.main()
