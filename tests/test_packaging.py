"""Packaging metadata tests for the fork distribution.

The package metadata in ``pyproject.toml`` must identify the fork, not the
upstream project: a distribution name that does not collide with the upstream
package, project URLs pointing at the fork's repository, the ``docker`` entry
point, the preserved MIT licence, and no reference to the upstream
organization.

Python 3.9 has no ``tomllib``, so the tests parse the file textually; the
relevant tables are simple ``key = "value"`` pairs.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TIER = "unit"

UPSTREAM_NAME = "docker-for-apple-container"
FORK_REPO = "fparrav/docker-for-apple-container"
ENTRY_POINT = "docker = \"container_docker_shim.cli:main\""


def read_pyproject() -> str:
    return (ROOT / "pyproject.toml").read_text()


def section(text: str, header: str) -> str:
    """Return the body of the named TOML table, or ``""`` when absent.

    The body runs until the next line that starts a table (``[``).
    """
    parts = text.split("\n" + header, 1)
    if len(parts) < 2:
        return ""
    body = parts[1]
    first_newline = body.find("\n")
    if first_newline == -1:
        return ""
    body = body[first_newline + 1 :]
    next_table = re.search(r"^\[", body, re.MULTILINE)
    if next_table:
        body = body[: next_table.start()]
    return body


def field(section_text: str, key: str) -> str | None:
    """Return the string value of a bare ``key = "value"`` line, or ``None``."""
    match = re.search(
        r"^\s*" + re.escape(key) + r'\s*=\s*"([^"]*)"',
        section_text,
        re.MULTILINE,
    )
    return match.group(1) if match else None


class PackagingMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = read_pyproject()
        self.project = section(self.text, "[project]")

    def test_name_differs_from_upstream(self) -> None:
        name = field(self.project, "name")
        self.assertIsNotNone(name, "pyproject.toml must declare a project name")
        self.assertNotEqual(
            name,
            UPSTREAM_NAME,
            "the fork must not reuse the upstream distribution name",
        )

    def test_project_urls_point_at_the_fork(self) -> None:
        urls = section(self.text, "[project.urls]")
        values = re.findall(r'=\s*"([^"]+)"', urls)
        self.assertTrue(values, "pyproject.toml must declare project URLs")
        for value in values:
            with self.subTest(url=value):
                self.assertIn(FORK_REPO, value)

    def test_entry_point_is_docker(self) -> None:
        self.assertIn(ENTRY_POINT, section(self.text, "[project.scripts]"))

    def test_license_is_preserved(self) -> None:
        self.assertEqual(field(self.project, "license"), "MIT")
        self.assertIn('"LICENSE"', self.project)

    def test_no_upstream_organization_references(self) -> None:
        self.assertNotIn("appautomaton", self.text.lower())

    def test_distribution_name_is_a_single_source(self) -> None:
        # D1: the name is read from pyproject.toml by the version lookup and
        # the release workflow, never hard-coded in a second place.
        name = field(self.project, "name")
        self.assertIsNotNone(name)
        self.assertEqual(
            len(re.findall(re.escape(name), self.text)),
            1,
            "the distribution name must appear exactly once in pyproject.toml",
        )


if __name__ == "__main__":
    unittest.main()
