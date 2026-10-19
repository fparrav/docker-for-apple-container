"""Unit tests for ``scripts/render_compat_table.py``.

The script lives outside the ``tests/`` package, so it is loaded from its
file path with ``importlib.util`` (the repo has no ``tests/__init__.py`` and
``discover -s tests`` puts ``tests/`` on ``sys.path`` for the rest of the
imports below).
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import compat_matrix  # noqa: E402


def _load_script_module():
    """Load ``scripts/render_compat_table.py`` as a module."""
    spec = importlib.util.spec_from_file_location(
        "render_compat_table", ROOT / "scripts" / "render_compat_table.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TIER = "unit"

render = _load_script_module()


ENTRIES = [
    {
        "id": "run",
        "kind": "command",
        "status": "SUPPORTED",
        "apple": "container run",
        "evidence": "unit",
        "test": None,
        "note": "translates to container run",
    },
    {
        "id": "run --memory",
        "kind": "option",
        "status": "PARTIAL",
        "apple": None,
        "evidence": "unit",
        "test": None,
        "note": "bytes accepted, units rejected",
    },
    {
        "id": "deploy.resources.limits.memory",
        "kind": "compose-key",
        "status": "UNSUPPORTED",
        "apple": None,
        "evidence": "unit",
        "test": None,
        "note": "dropped with a warning",
    },
]

BLOCK = render.render_block(ENTRIES)


class RenderBlockTests(unittest.TestCase):
    def test_starts_and_ends_with_markers(self):
        self.assertTrue(BLOCK.startswith(render.START + "\n"))
        self.assertTrue(BLOCK.endswith("\n" + render.END))

    def test_matches_render_table(self):
        self.assertEqual(
            BLOCK, f"{render.START}\n{compat_matrix.render_table(ENTRIES)}\n{render.END}"
        )


class ReplaceBlockTests(unittest.TestCase):
    def test_keeps_surrounding_text(self):
        text = f"# Title\n\nbefore\n{BLOCK}\nafter\n"
        self.assertEqual(render.replace_block(text, BLOCK), text)

    def test_replaces_stale_block(self):
        stale = f"before\n{render.START}\nold table\n{render.END}\nafter"
        self.assertEqual(render.replace_block(stale, BLOCK), f"before\n{BLOCK}\nafter")

    def test_idempotent(self):
        stale = f"before\n{render.START}\nold table\n{render.END}\nafter"
        once = render.replace_block(stale, BLOCK)
        self.assertEqual(render.replace_block(once, BLOCK), once)

    def test_missing_markers_raise(self):
        with self.assertRaises(ValueError):
            render.replace_block("no markers here", BLOCK)
        with self.assertRaises(ValueError):
            render.replace_block(f"before\n{render.END}\nafter", BLOCK)
        with self.assertRaises(ValueError):
            render.replace_block(f"{render.START}\nbody", BLOCK)

    def test_reversed_markers_raise(self):
        with self.assertRaises(ValueError):
            render.replace_block(f"{render.END}\n{render.START}\nbody", BLOCK)


class CheckFileTests(unittest.TestCase):
    def test_check_current_file_exits_zero(self):
        current = render.render_block(compat_matrix.load())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "README.md"
            path.write_text(f"# Title\n\n{current}\nafter\n", encoding="utf-8")
            self.assertEqual(render.check_file(path), 0)

    def test_check_stale_file_exits_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "README.md"
            path.write_text(
                f"# Title\n\n{render.START}\nold table\n{render.END}\nafter\n",
                encoding="utf-8",
            )
            self.assertEqual(render.check_file(path), 1)

    def test_write_missing_markers_exits_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "README.md"
            path.write_text("no markers here\n", encoding="utf-8")
            self.assertEqual(render.write_file(path), 1)
            self.assertEqual(path.read_text(encoding="utf-8"), "no markers here\n")


if __name__ == "__main__":
    unittest.main()
