"""Tests for compose_reconcile.config_hash (the recreation decision hash).

The specs are parsed with the shim's own ``compose.parse_yaml`` so the
hash is exercised against what the translator actually sees, not
hand-built dicts.
"""
from __future__ import annotations

import copy
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim import compose  # noqa: E402
from container_docker_shim import compose_reconcile  # noqa: E402


def _service(yaml_text: str) -> dict:
    """Parse ``yaml_text`` and return the ``web`` service mapping."""
    doc = compose.parse_yaml(yaml_text)
    return doc["services"]["web"]


IMAGE_ID = "sha256:0123456789abcdef"


class KeyOrderTests(unittest.TestCase):
    def test_key_order_does_not_change_hash(self):
        first = _service(
            textwrap.dedent(
                """\
                services:
                  web:
                    image: nginx:latest
                    environment:
                      FOO: 1
                      BAR: 2
                """
            )
        )
        second = _service(
            textwrap.dedent(
                """\
                services:
                  web:
                    environment:
                      BAR: 2
                      FOO: 1
                    image: nginx:latest
                """
            )
        )
        self.assertEqual(
            compose_reconcile.config_hash(first, IMAGE_ID),
            compose_reconcile.config_hash(second, IMAGE_ID),
        )

    def test_list_environment_same_item_order_same_hash(self):
        # List item order is significant, so both texts list the same
        # items; only the key order around the list differs.
        first = _service(
            textwrap.dedent(
                """\
                services:
                  web:
                    image: nginx:latest
                    environment:
                      - FOO=1
                """
            )
        )
        second = _service(
            textwrap.dedent(
                """\
                services:
                  web:
                    environment:
                      - FOO=1
                    image: nginx:latest
                """
            )
        )
        self.assertEqual(
            compose_reconcile.config_hash(first, IMAGE_ID),
            compose_reconcile.config_hash(second, IMAGE_ID),
        )


class WhitespaceTests(unittest.TestCase):
    def test_plain_scalar_spaces_are_stripped_by_parser(self):
        # parse_yaml strips the spaces around unquoted plain scalars, so
        # the two texts yield identical values.
        with_spaces = _service(
            textwrap.dedent(
                """\
                services:
                  web:
                    image:   nginx:latest
                    environment:
                      FOO: bar
                """
            ).replace("image:   nginx:latest", "image:   nginx:latest   ").replace(
                "FOO: bar", "FOO: bar   "
            )
        )
        without_spaces = _service(
            textwrap.dedent(
                """\
                services:
                  web:
                    image: nginx:latest
                    environment:
                      FOO: bar
                """
            )
        )
        self.assertEqual(
            compose_reconcile.config_hash(with_spaces, IMAGE_ID),
            compose_reconcile.config_hash(without_spaces, IMAGE_ID),
        )

    def test_comments_blank_lines_and_key_order_do_not_change_hash(self):
        decorated = _service(
            textwrap.dedent(
                """\
                services:
                  web:
                    image: nginx:latest
                    # a comment line

                    environment:

                      FOO: 1
                """
            )
        )
        plain = _service(
            textwrap.dedent(
                """\
                services:
                  web:
                    environment:
                      FOO: 1
                    image: nginx:latest
                """
            )
        )
        self.assertEqual(
            compose_reconcile.config_hash(decorated, IMAGE_ID),
            compose_reconcile.config_hash(plain, IMAGE_ID),
        )


class ValueChangeTests(unittest.TestCase):
    def test_environment_change_changes_hash(self):
        foo1 = _service(
            textwrap.dedent(
                """\
                services:
                  web:
                    image: nginx:latest
                    environment:
                      - FOO=1
                """
            )
        )
        foo2 = _service(
            textwrap.dedent(
                """\
                services:
                  web:
                    image: nginx:latest
                    environment:
                      - FOO=2
                """
            )
        )
        self.assertNotEqual(
            compose_reconcile.config_hash(foo1, IMAGE_ID),
            compose_reconcile.config_hash(foo2, IMAGE_ID),
        )

    def test_image_id_change_changes_hash(self):
        spec = _service(
            textwrap.dedent(
                """\
                services:
                  web:
                    image: nginx:latest
                """
            )
        )
        self.assertNotEqual(
            compose_reconcile.config_hash(spec, IMAGE_ID),
            compose_reconcile.config_hash(spec, "sha256:deadbeef"),
        )

    def test_image_tag_change_changes_hash(self):
        tag_125 = _service(
            textwrap.dedent(
                """\
                services:
                  web:
                    image: nginx:1.25
                """
            )
        )
        tag_126 = _service(
            textwrap.dedent(
                """\
                services:
                  web:
                    image: nginx:1.26
                """
            )
        )
        self.assertNotEqual(
            compose_reconcile.config_hash(tag_125, IMAGE_ID),
            compose_reconcile.config_hash(tag_126, IMAGE_ID),
        )


class IgnoredKeysTests(unittest.TestCase):
    def test_depends_on_build_and_x_extensions_are_ignored(self):
        base = _service(
            textwrap.dedent(
                """\
                services:
                  web:
                    image: nginx:latest
                    environment:
                      - FOO=1
                """
            )
        )
        with_extras = _service(
            textwrap.dedent(
                """\
                services:
                  web:
                    image: nginx:latest
                    environment:
                      - FOO=1
                    depends_on:
                      - db
                    build: ./web
                    x-note: hello
                """
            )
        )
        changed_extras = _service(
            textwrap.dedent(
                """\
                services:
                  web:
                    image: nginx:latest
                    environment:
                      - FOO=1
                    depends_on: redis
                    build: ./web-v2
                    x-note: something else
                """
            )
        )
        self.assertEqual(
            compose_reconcile.config_hash(base, IMAGE_ID),
            compose_reconcile.config_hash(with_extras, IMAGE_ID),
        )
        self.assertEqual(
            compose_reconcile.config_hash(with_extras, IMAGE_ID),
            compose_reconcile.config_hash(changed_extras, IMAGE_ID),
        )


class NoMutationTests(unittest.TestCase):
    def test_spec_is_not_mutated(self):
        spec = _service(
            textwrap.dedent(
                """\
                services:
                  web:
                    image: nginx:latest
                    environment:
                      FOO: 1
                    depends_on:
                      - db
                    build: ./web
                    x-note: hello
                """
            )
        )
        snapshot = copy.deepcopy(spec)
        compose_reconcile.config_hash(spec, IMAGE_ID)
        self.assertEqual(spec, snapshot)


WANTED_HASH = "h1"
WANTED_IMAGE = "img1"


def _container(
    state: str = "running",
    hash_value: str = WANTED_HASH,
    image: str | None = WANTED_IMAGE,
) -> dict:
    """Build an ``existing`` dict for :func:`compose_reconcile.decide`.

    ``hash_value`` and ``image`` default to the wanted values so callers
    only pass what they want to change; ``image=None`` omits the
    :data:`compose_reconcile.LABEL_IMAGE` label entirely.
    """
    labels = {compose_reconcile.LABEL_CONFIG_HASH: hash_value}
    if image is not None:
        labels[compose_reconcile.LABEL_IMAGE] = image
    return {"labels": labels, "state": state}


class DecideCreateTests(unittest.TestCase):
    def test_missing_container_creates(self):
        for kwargs in ({}, {"force": True}, {"no_recreate": True}):
            with self.subTest(kwargs):
                self.assertEqual(
                    compose_reconcile.decide(
                        None, WANTED_HASH, WANTED_IMAGE, **kwargs
                    ),
                    "create",
                )


class DecideKeepStartTests(unittest.TestCase):
    def test_matching_running_container_is_kept(self):
        self.assertEqual(
            compose_reconcile.decide(
                _container("running"), WANTED_HASH, WANTED_IMAGE
            ),
            "keep",
        )

    def test_matching_stopped_container_is_started(self):
        for state in ("stopped", "exited"):
            with self.subTest(state=state):
                self.assertEqual(
                    compose_reconcile.decide(
                        _container(state), WANTED_HASH, WANTED_IMAGE
                    ),
                    "start",
                )


class DecideRecreateTests(unittest.TestCase):
    def test_hash_mismatch_recreates(self):
        for state in ("running", "stopped"):
            with self.subTest(state=state):
                self.assertEqual(
                    compose_reconcile.decide(
                        _container(state), "other", WANTED_IMAGE
                    ),
                    "recreate",
                )

    def test_missing_labels_recreate(self):
        for labels in ({}, None):
            with self.subTest(labels=labels):
                self.assertEqual(
                    compose_reconcile.decide(
                        {"labels": labels, "state": "running"},
                        WANTED_HASH,
                        WANTED_IMAGE,
                    ),
                    "recreate",
                )

    def test_image_mismatch_recreates(self):
        self.assertEqual(
            compose_reconcile.decide(
                _container("running"), WANTED_HASH, "other"
            ),
            "recreate",
        )

    def test_missing_image_label_recreates(self):
        self.assertEqual(
            compose_reconcile.decide(
                _container("running", image=None), WANTED_HASH, WANTED_IMAGE
            ),
            "recreate",
        )


class DecideForceTests(unittest.TestCase):
    def test_force_recreates_identical_container(self):
        for state in ("running", "stopped"):
            with self.subTest(state=state):
                self.assertEqual(
                    compose_reconcile.decide(
                        _container(state),
                        WANTED_HASH,
                        WANTED_IMAGE,
                        force=True,
                    ),
                    "recreate",
                )

    def test_force_and_no_recreate_are_incompatible(self):
        with self.assertRaises(ValueError):
            compose_reconcile.decide(
                _container("running"),
                WANTED_HASH,
                WANTED_IMAGE,
                force=True,
                no_recreate=True,
            )


class DecideNoRecreateTests(unittest.TestCase):
    def test_changed_running_container_is_kept(self):
        self.assertEqual(
            compose_reconcile.decide(
                _container("running"),
                "other",
                WANTED_IMAGE,
                no_recreate=True,
            ),
            "keep",
        )

    def test_changed_stopped_container_is_started(self):
        self.assertEqual(
            compose_reconcile.decide(
                _container("stopped"),
                "other",
                WANTED_IMAGE,
                no_recreate=True,
            ),
            "start",
        )

    def test_running_container_without_labels_is_kept(self):
        self.assertEqual(
            compose_reconcile.decide(
                {"labels": {}, "state": "running"},
                WANTED_HASH,
                WANTED_IMAGE,
                no_recreate=True,
            ),
            "keep",
        )

    def test_unchanged_container_is_untouched(self):
        for state, expected in (("running", "keep"), ("stopped", "start")):
            with self.subTest(state=state):
                self.assertEqual(
                    compose_reconcile.decide(
                        _container(state),
                        WANTED_HASH,
                        WANTED_IMAGE,
                        no_recreate=True,
                    ),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
