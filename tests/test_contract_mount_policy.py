"""Contract test CT2 (design D8): one verdict per mount option, on every surface.

Iterates every entry of ``mounts.MODE_RULES`` (short syntax) and every entry
of ``mounts.ATTRIBUTE_RULES`` (long syntax; structural keys are not options
and are skipped) and asserts that all four surfaces return the same verdict,
derived from the table:

1. ``docker run -v`` via ``mounts.parse_volume_spec`` (bind and named sources);
2. Compose short syntax through the built-in parser path
   ``compose._entry_mount``;
3. Compose long syntax (normalizer-shaped mapping) through
   ``compose._entry_mount``;
4. the capability gate ``compose_capabilities.validate``: a refusal exists iff
   the verdict is REFUSED, and the refusal names the option and the target.

Attributes without a short-syntax spelling (``bind.propagation``,
``volume.nocopy``, ...) cannot be expressed by surfaces 1 and 2, so they are
checked on the long-syntax surfaces only. The comparison is a
``disagreements()`` helper that returns the mismatch descriptions (empty when
every surface agrees with the table); a patched copy of the table and a
patched surface function prove the check itself can fail.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim import compose  # noqa: E402
from container_docker_shim import compose_capabilities as caps  # noqa: E402
from container_docker_shim import mounts  # noqa: E402

TIER = "unit"

PROJECT_DIR = "/project"

BIND = {"type": "bind", "source": "./data", "target": "/data"}
NAMED = {"type": "volume", "source": "cache", "target": "/cache"}

# The value the normalizer would carry for each long-syntax attribute.
ATTRIBUTE_VALUES = {
    "read_only": True,
    "consistency": "cached",
    "bind.create_host_path": True,
    "bind.selinux": "z",
    "bind.propagation": "rshared",
    "volume.nocopy": True,
    "volume.subpath": "/sub",
}

# Long-syntax attributes that also have a short-syntax spelling.
SHORT_SPELLING = {"read_only": "ro", "consistency": "cached", "bind.selinux": "z"}


def _project() -> compose.Project:
    return compose.Project("proj", {"services": {"web": {"image": "x"}}}, PROJECT_DIR)


def _long_entry(name: str) -> dict:
    """A normalizer-shaped long-syntax mapping carrying one attribute."""
    if name == "read_only":
        return {**BIND, "read_only": True}
    if name == "consistency":
        return {**NAMED, "consistency": "cached"}
    if name == "image":
        return {"type": "image", "source": "img", "target": "/img"}
    if name == "tmpfs":
        return {"type": "tmpfs", "target": "/scratch"}
    section, _, sub = name.partition(".")
    base = BIND if section == "bind" else NAMED
    return {**base, section: {sub: ATTRIBUTE_VALUES[name]}}


def _long_equivalent(mode: str) -> dict:
    """The long-syntax mapping the normalizer emits for a short mode."""
    if mode == "ro":
        return {**BIND, "read_only": True}
    if mode == "rw":
        return {**BIND, "read_only": False}
    if mode in ("cached", "delegated"):
        return {**NAMED, "consistency": mode}
    return {**BIND, "bind": {"selinux": mode}}


def _probe_run_v(entry: str, project: compose.Project) -> tuple[str, str]:
    """Surface 1: ``docker run -v`` (``mounts.parse_volume_spec``)."""
    try:
        mount = mounts.parse_volume_spec(entry, cwd=PROJECT_DIR)
    except mounts.MountSpecError as exc:
        return mounts.REFUSED, str(exc)
    return (mounts.READ_ONLY if mount.readonly else mounts.NO_EFFECT), ""


def _probe_compose_short(entry: str, project: compose.Project) -> tuple[str, str]:
    """Surface 2: Compose short syntax through the built-in parser."""
    try:
        planned = compose._entry_mount(entry, project)
    except mounts.MountSpecError as exc:
        return mounts.REFUSED, str(exc)
    return (mounts.READ_ONLY if planned.mount.readonly else mounts.NO_EFFECT), ""


def _probe_compose_long(entry: dict, project: compose.Project) -> tuple[str, str]:
    """Surface 3: Compose long syntax (normalizer-shaped mapping)."""
    try:
        planned = compose._entry_mount(entry, project)
    except mounts.MountSpecError as exc:
        return mounts.REFUSED, str(exc)
    return (mounts.READ_ONLY if planned.mount.readonly else mounts.NO_EFFECT), ""


def _probe_gate(entry, project: compose.Project) -> tuple[str, str]:
    """Surface 4: the capability gate (a refusal exists iff the policy refuses)."""
    report = caps.validate({}, [("web", {"image": "x", "volumes": [entry]})], {})
    if not report.refusals:
        return "accepted", ""
    return mounts.REFUSED, " | ".join(finding.message for finding in report.refusals)


def _compare(
    option: str,
    surface: str,
    observed: str,
    message: str,
    verdict: str,
    target: str,
    name: str,
) -> list[str]:
    """Mismatch descriptions for one option on one surface (empty = agrees)."""
    if observed != verdict:
        # The gate only distinguishes refused from accepted; read-only and
        # no-effect both read as "accepted" there, so a non-refusal mismatch
        # is not a disagreement.
        if verdict == mounts.REFUSED or observed == mounts.REFUSED:
            return [
                f"{option}: {surface} returned {observed}, table says {verdict} "
                f"(target {target})"
            ]
        return []
    if verdict == mounts.REFUSED and name and (name not in message or target not in message):
        return [
            f"{option}: {surface} refusal does not name {name!r} or {target!r}: {message!r}"
        ]
    return []


def _mode_checks(mode: str) -> list[tuple[str, object, object, str, str]]:
    """(surface, probe, entry, target, name-in-message) rows for one short mode."""
    bind_value = f"./data:/data:{mode}"
    named_value = f"cache:/cache:{mode}"
    long_entry = _long_equivalent(mode)
    long_name = "selinux" if mode in ("z", "Z") else ""
    return [
        ("run -v bind", _probe_run_v, bind_value, "/data", mode),
        ("run -v named", _probe_run_v, named_value, "/cache", mode),
        ("compose short", _probe_compose_short, bind_value, "/data", mode),
        ("compose long", _probe_compose_long, long_entry, long_entry["target"], long_name),
        ("gate short", _probe_gate, bind_value, "/data", mode),
        ("gate long", _probe_gate, long_entry, long_entry["target"], long_name),
    ]


def _attribute_checks(name: str) -> list[tuple[str, object, object, str, str]]:
    """Surface rows for one long-syntax attribute (short forms where they exist)."""
    long_entry = _long_entry(name)
    checks = [
        ("compose long", _probe_compose_long, long_entry, long_entry["target"], name),
        ("gate long", _probe_gate, long_entry, long_entry["target"], name),
    ]
    spelling = SHORT_SPELLING.get(name)
    if spelling:
        value = f"./data:/data:{spelling}"
        checks += [
            (f"run -v {spelling!r}", _probe_run_v, value, "/data", spelling),
            (f"compose short {spelling!r}", _probe_compose_short, value, "/data", spelling),
            (f"gate short {spelling!r}", _probe_gate, value, "/data", spelling),
        ]
    return checks


def disagreements() -> list[str]:
    """Every surface whose verdict disagrees with the table (empty = all agree)."""
    project = _project()
    problems = []
    for mode, verdict in mounts.MODE_RULES.items():
        for surface, probe, entry, target, name in _mode_checks(mode):
            observed, message = probe(entry, project)
            problems.extend(_compare(mode, surface, observed, message, verdict, target, name))
    for name, verdict in mounts.ATTRIBUTE_RULES.items():
        if name in mounts.STRUCTURAL_KEYS or name in mounts.LONG_SECTIONS:
            continue  # structural keys are not options
        for surface, probe, entry, target, message_name in _attribute_checks(name):
            observed, message = probe(entry, project)
            problems.extend(
                _compare(name, surface, observed, message, verdict, target, message_name)
            )
    return problems


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = _project()

    def test_every_surface_agrees_with_the_table(self):
        self.assertEqual(disagreements(), [])

    def test_short_and_long_emit_the_same_mount_argument(self):
        short = compose._entry_mount("./data:/data:ro", self.project)
        long = compose._entry_mount({**BIND, "read_only": True}, self.project)
        self.assertEqual(mounts.emit(short.mount), mounts.emit(long.mount))
        self.assertEqual(
            mounts.emit(short.mount),
            ["--mount", f"type=bind,source={PROJECT_DIR}/data,target=/data,readonly"],
        )

    def test_empty_selinux_is_accepted_by_long_syntax_and_gate(self):
        for selinux in ("", None):
            entry = {**BIND, "bind": {"selinux": selinux}}
            with self.subTest(selinux=selinux):
                planned = compose._entry_mount(entry, self.project)
                self.assertFalse(planned.mount.readonly)
                report = caps.validate({}, [("web", {"image": "x", "volumes": [entry]})], {})
                self.assertEqual(report.refusals, [])


class NegativeCheckTests(unittest.TestCase):
    """The comparison must fail when a surface is made to disagree."""

    def test_patched_table_is_detected(self):
        with mock.patch.dict(mounts.MODE_RULES, {"z": mounts.READ_ONLY}):
            self.assertTrue(disagreements())

    def test_patched_surface_is_detected(self):
        with mock.patch.object(mounts, "check_short_modes", lambda mode, target: False):
            self.assertTrue(disagreements())


if __name__ == "__main__":
    unittest.main()
