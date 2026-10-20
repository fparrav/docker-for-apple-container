"""Compatibility matrix coverage checks.

``test_compat_matrix_schema`` only checks the matrix schema. These tests check
that the matrix *covers* the translator: every ``docker run`` and
``docker build`` option in the ``cli`` option sets and every Compose service
key the ``compose`` translator knows about must have an entry, every
``live``/``conformance`` evidence entry must name a test that actually exists,
and the README block between the compat-matrix markers must equal the rendered
table (the fix for a stale README is ``scripts/render_compat_table.py``).

When a coverage test fails the fix is to add the missing matrix entry (or fix
the broken test id in the entry); the ``cli``/``compose`` option sets are the
source of truth and must not be edited to satisfy this file.
"""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import compat_matrix  # noqa: E402

from container_docker_shim import cli  # noqa: E402
from container_docker_shim import compose  # noqa: E402
from container_docker_shim import compose_capabilities  # noqa: E402


TIER = "unit"

# The README block carrying the rendered matrix; mirrors the markers in
# scripts/render_compat_table.py.
MATRIX_START = "<!-- compat-matrix:start -->"
MATRIX_END = "<!-- compat-matrix:end -->"


def _entry_ids(entries) -> set:
    return {
        entry.get("id")
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }


def _resolve_test_id(test_id: str) -> str:
    """Resolve ``module.Class.method`` by importing; return an error or None.

    Importing a live test module only defines its classes (the tests run only
    under the live tier), so the import itself executes nothing.
    """
    parts = test_id.split(".")
    if len(parts) < 3:
        return f"{test_id!r} is not of the form module.Class.method"
    module_name, class_name, method_name = parts[0], parts[1], ".".join(parts[2:])
    try:
        module = importlib.import_module(module_name)
    except ImportError as error:
        return f"{test_id!r}: cannot import {module_name!r} ({error})"
    klass = getattr(module, class_name, None)
    if klass is None:
        return f"{test_id!r}: {module_name} has no class {class_name!r}"
    if not callable(getattr(klass, method_name, None)):
        return f"{test_id!r}: {module_name}.{class_name} has no method {method_name!r}"
    return None


def _split_row(row: str) -> list:
    """Split a Markdown table row into cells.

    ``render_table`` escapes pipes inside cells as ``\\|``, so splitting on
    space-pipe-space never breaks a cell that contains a literal pipe.
    """
    inner = row.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [cell.strip() for cell in inner.split(" | ")]


def _row_pairs(block: str) -> list:
    """Return ``(id, row)`` pairs for the data rows of a rendered table.

    The first two rows are the header and separator emitted by
    ``compat_matrix.render_table``; the first cell of every data row is the
    entry id.
    """
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    pairs = []
    for line in lines[2:]:
        cells = _split_row(line)
        pairs.append((cells[0] if cells else line, line))
    return pairs


def _describe_row_diff(readme_block: str, rendered: str) -> str:
    """Name the entry ids whose rows differ between two table blocks."""
    readme_rows = dict(_row_pairs(readme_block))
    rendered_rows = dict(_row_pairs(rendered))
    missing = [
        entry_id for entry_id in rendered_rows if entry_id not in readme_rows
    ]
    extra = [
        entry_id for entry_id in readme_rows if entry_id not in rendered_rows
    ]
    changed = [
        entry_id
        for entry_id, row in rendered_rows.items()
        if entry_id in readme_rows and readme_rows[entry_id] != row
    ]
    common_readme = [i for i in readme_rows if i in rendered_rows]
    common_rendered = [i for i in rendered_rows if i in readme_rows]
    parts = []
    if common_readme != common_rendered:
        moved = [
            entry_id
            for entry_id, expected in zip(common_readme, common_rendered)
            if entry_id != expected
        ]
        parts.append("out of order: " + ", ".join(moved))
    if missing:
        parts.append("missing from README: " + ", ".join(missing))
    if extra:
        parts.append("missing from matrix: " + ", ".join(extra))
    if changed:
        parts.append("changed: " + ", ".join(changed))
    return "; ".join(parts) if parts else "rows differ"


# Short forms are covered through the long option they alias. A short option
# that appears in an option set but not here fails the test, so a new short
# option cannot slip in without a matrix entry.
# Note: -c is now refused (Docker CPU-shares vs Apple --cpus count) and does
# not map to --cpus anymore; mapped to None so the alias checker skips it.
RUN_SHORT_ALIASES = {
    "-c": None,
    "-d": "--detach",
    "-e": "--env",
    "-i": "--interactive",
    "-it": "--interactive",
    "-l": "--label",
    "-m": "--memory",
    "-p": "--publish",
    "-t": "--tty",
    "-ti": "--interactive",
    "-u": "--user",
    "-v": "--volume",
    "-w": "--workdir",
}
BUILD_SHORT_ALIASES = {
    "-c": None,
    "-f": "--file",
    "-h": None,  # --help is not a matrix entry
    "-m": "--memory",
    "-o": "--output",
    "-q": "--quiet",
    "-t": "--tag",
}


class MatrixCoverageTests(unittest.TestCase):
    def setUp(self):
        self.entries = compat_matrix.load()
        self.ids = _entry_ids(self.entries)

    def test_every_run_option_has_an_entry(self):
        union = (
            cli.RUN_VALUE_OPTIONS
            | cli.RUN_BOOL_OPTIONS
            | cli.RUN_REFUSED_OPTIONS
            | cli.RUN_EXPLICIT_OPTIONS
        )
        options = sorted(option for option in union if option.startswith("--"))
        missing = [
            option for option in options if f"run {option}" not in self.ids
        ]
        self.assertEqual(
            missing,
            [],
            "matrix entries missing for run options: " + ", ".join(missing),
        )

    def test_every_build_option_has_an_entry(self):
        union = (
            cli.BUILD_VALUE_OPTIONS
            | cli.BUILD_BOOL_OPTIONS
            | cli.BUILD_NOOP_OPTIONS
            | cli.BUILD_REFUSED_OPTIONS
            | cli.BUILD_EXPLICIT_OPTIONS
        )
        options = sorted(
            option
            for option in union
            if option.startswith("--") and option != "--help"
        )
        missing = [
            option for option in options if f"build {option}" not in self.ids
        ]
        self.assertEqual(
            missing,
            [],
            "matrix entries missing for build options: " + ", ".join(missing),
        )

    def _check_short_aliases(self, prefix, union, aliases):
        shorts = sorted(option for option in union if not option.startswith("--"))
        unmapped = [option for option in shorts if option not in aliases]
        self.assertEqual(
            unmapped,
            [],
            f"short {prefix} options without a long-form alias: "
            + ", ".join(unmapped),
        )
        missing = [
            f"{short} -> {long}"
            for short, long in sorted(aliases.items())
            if short in union and long and f"{prefix} {long}" not in self.ids
        ]
        self.assertEqual(
            missing,
            [],
            f"short {prefix} aliases whose long form has no entry: "
            + ", ".join(missing),
        )

    def test_every_short_run_option_maps_to_an_entry(self):
        union = (
            cli.RUN_VALUE_OPTIONS
            | cli.RUN_BOOL_OPTIONS
            | cli.RUN_REFUSED_OPTIONS
            | cli.RUN_EXPLICIT_OPTIONS
        )
        self._check_short_aliases("run", union, RUN_SHORT_ALIASES)

    def test_every_short_build_option_maps_to_an_entry(self):
        union = (
            cli.BUILD_VALUE_OPTIONS
            | cli.BUILD_BOOL_OPTIONS
            | cli.BUILD_NOOP_OPTIONS
            | cli.BUILD_REFUSED_OPTIONS
            | cli.BUILD_EXPLICIT_OPTIONS
        )
        self._check_short_aliases("build", union, BUILD_SHORT_ALIASES)

    def test_every_compose_key_has_an_entry(self):
        caps = compose_capabilities
        wanted = {f"compose {key}" for key in compose.SUPPORTED_SERVICE_KEYS}
        wanted |= {f"compose {key}" for key in compose.UNSUPPORTED_SERVICE_KEYS}
        wanted |= {f"compose {key}" for key in compose.SECURITY_OPTION_KEYS}
        wanted |= {f"compose {key}" for key in caps.SERVICE_KEYS}
        wanted |= {f"compose build.{key}" for key in caps.BUILD_KEYS}
        wanted |= {f"compose deploy.{key}" for key in caps.DEPLOY_KEYS}
        wanted |= {f"compose volumes.{key}" for key in caps.MOUNT_OPTIONS}
        for kind, attrs in caps.RESOURCE_ATTRIBUTES.items():
            wanted |= {f"compose top-level {kind}.{key}" for key in attrs}
        missing = sorted(entry for entry in wanted if entry not in self.ids)
        self.assertEqual(
            missing,
            [],
            "matrix entries missing for classified compose keys: " + ", ".join(missing),
        )

    def test_matrix_status_matches_the_capability_table(self):
        by_id = {e["id"]: e for e in self.entries if isinstance(e, dict)}
        wrong = []
        for key, rule in compose_capabilities.SERVICE_KEYS.items():
            entry = by_id.get(f"compose {key}")
            if entry is None:
                continue
            # A key the table accepts may be PARTIAL in the matrix because
            # its sub-keys carry limits that have their own table entries.
            allowed = {rule.status}
            if rule.status == compose_capabilities.SUPPORTED:
                allowed.add(compose_capabilities.PARTIAL)
            if entry["status"] not in allowed:
                wrong.append(f"{key}: matrix {entry['status']} vs table {rule.status}")
        self.assertEqual(wrong, [], "; ".join(wrong))

    def test_live_entries_name_existing_tests(self):
        broken = []
        for entry in self.entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("evidence") not in ("live", "conformance"):
                continue
            test_id = entry.get("test")
            if not test_id:
                continue  # schema gap already reported by compat_matrix.validate
            error = _resolve_test_id(test_id)
            if error:
                broken.append(error)
        self.assertEqual(
            broken,
            [],
            "matrix entries naming unresolvable tests: " + "; ".join(broken),
        )

    def test_readme_block_matches_rendered_table(self):
        readme = ROOT / "README.md"
        text = readme.read_text(encoding="utf-8")
        start = text.find(MATRIX_START)
        end = text.find(MATRIX_END)
        self.assertNotEqual(
            start, -1, f"{readme.name} is missing the {MATRIX_START!r} marker"
        )
        self.assertNotEqual(
            end, -1, f"{readme.name} is missing the {MATRIX_END!r} marker"
        )
        self.assertGreater(
            end,
            start,
            f"{readme.name} compat-matrix end marker precedes the start marker",
        )

        rendered = compat_matrix.render_table(self.entries)
        in_readme = text[start + len(MATRIX_START) : end].strip()
        self.assertEqual(
            in_readme,
            rendered,
            "README compat-matrix block differs from the rendered table "
            "(run scripts/render_compat_table.py --write README.md to refresh): "
            + _describe_row_diff(in_readme, rendered),
        )

    def test_matrix_is_valid(self):
        self.assertEqual(compat_matrix.validate(self.entries), [])


if __name__ == "__main__":
    unittest.main()
