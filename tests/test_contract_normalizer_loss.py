"""Contract test CT1 (design D8): the normalizer loses nothing silently.

For every recorded ``docker-compose config --format json`` fixture under
``tests/fixtures/compose/normalized/``, every top-level key of the raw
output must be either preserved in the ``convert_model`` output, listed in
``compose_normalizer.DROPPED_TOP_LEVEL``, or an ``x-`` extension key (the
normalizer drops those on purpose; their names are not fixed). The model's
``secrets`` must equal exactly the secret names referenced by a service
``build.secrets`` entry (a string, or a mapping with ``source``) that also
have a top-level definition in the fixture. The check is a helper that
returns the offending keys, so a key someone adds to a fixture without
listing it fails the suite; a mutated copy of a fixture proves the check
itself can fail.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim import compose_normalizer  # noqa: E402

TIER = "unit"

VERSION = "5.6.0"
FIXTURE_DIR = (
    Path(__file__).resolve().parent / "fixtures" / "compose" / "normalized"
)


def _load_fixture(path: Path) -> dict:
    text = path.read_text(encoding="utf-8").replace("__PROJECT_DIR__", "/project")
    return json.loads(text)


def _offending_top_level_keys(raw: dict) -> list:
    """Top-level keys ``convert_model`` loses without listing them.

    A key is acceptable when it is present in the model, listed in
    ``DROPPED_TOP_LEVEL``, or is an ``x-`` extension key. Returns the
    offenders sorted, so an empty list means the fixture loses nothing
    undocumented.
    """
    model = compose_normalizer.convert_model(raw, VERSION)
    allowed = set(model) | set(compose_normalizer.DROPPED_TOP_LEVEL)
    return sorted(
        key for key in raw if key not in allowed and not key.startswith("x-")
    )


def _referenced_secret_names(raw: dict) -> set:
    """Secret names every service ``build.secrets`` entry references.

    Mirrors the reference rule of ``convert_model``: a string entry is its
    own name, a mapping entry names its ``source``, and a build that is a
    plain string (or absent) contributes nothing.
    """
    names = set()
    services = raw.get("services") or {}
    if not isinstance(services, dict):
        return names
    for service in services.values():
        if not isinstance(service, dict):
            continue
        build = service.get("build")
        if not isinstance(build, dict):
            continue
        for entry in build.get("secrets") or []:
            if isinstance(entry, str):
                source = entry
            elif isinstance(entry, dict):
                source = entry.get("source")
            else:
                source = None
            if isinstance(source, str):
                names.add(source)
    return names


class NormalizerLossContractTests(unittest.TestCase):
    """CT1: no fixture top-level key is lost without being listed."""

    @classmethod
    def setUpClass(cls):
        cls.fixtures = sorted(FIXTURE_DIR.glob("*.json"))

    def test_fixtures_exist(self):
        self.assertTrue(
            self.fixtures,
            "no normalized fixtures found under %s" % FIXTURE_DIR,
        )

    def test_every_top_level_key_is_preserved_listed_or_extension(self):
        for path in self.fixtures:
            with self.subTest(fixture=path.name):
                raw = _load_fixture(path)
                self.assertEqual(
                    _offending_top_level_keys(raw),
                    [],
                    "top-level keys dropped without being listed in "
                    "DROPPED_TOP_LEVEL",
                )

    def test_model_secrets_equal_the_build_referenced_definitions(self):
        for path in self.fixtures:
            with self.subTest(fixture=path.name):
                raw = _load_fixture(path)
                model = compose_normalizer.convert_model(raw, VERSION)
                definitions = raw.get("secrets") or {}
                expected = _referenced_secret_names(raw) & set(definitions)
                self.assertEqual(set(model["secrets"]), expected)

    def test_an_unlisted_key_is_reported(self):
        # The check must actually fail: a copy of one fixture with an
        # unlisted top-level key yields exactly that key as offender.
        raw = copy.deepcopy(_load_fixture(self.fixtures[0]))
        raw["surprise"] = {}
        self.assertEqual(_offending_top_level_keys(raw), ["surprise"])


if __name__ == "__main__":
    unittest.main()
