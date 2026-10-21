"""Contract test CT4 (design D1): one verdict per resource declaration.

A table of top-level volume and network declarations, each with the verdict the
specs require, is compared with every surface that decides on it. The verdict of
a volume is ``bind`` (the local bind idiom, translated), ``plain`` (an ordinary
named volume the shim keeps managing) or ``refused`` (naming the option); the
verdict of a network option set is the list of options that are not applied and
the list that are refused.

Surfaces, added as their tasks land:

1. the pure module ``compose_resource_compat``;
2. the capability gate ``compose_capabilities.validate`` (task 3.1);
3. the translator ``compose._entry_mount`` (task 3.2).

``disagreements()`` returns the mismatch descriptions (empty when every surface
agrees with the table); a patched table and a patched surface prove the check
itself can fail.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim import compose  # noqa: E402
from container_docker_shim import compose_capabilities as caps  # noqa: E402
from container_docker_shim import compose_resource_compat as compat  # noqa: E402
from container_docker_shim import mounts  # noqa: E402

TIER = "unit"

BIND, PLAIN, REFUSED = "bind", "plain", "refused"
# The gate only separates accepted from refused; it cannot tell a bind from a
# plain volume, which is the translator's decision.
ACCEPTED = "accepted"

BRIDGE = "com.docker.network.bridge.name"
MTU = "com.docker.network.driver.mtu"


def _volume(driver=None, **opts) -> dict:
    spec = {}
    if driver is not None:
        spec["driver"] = driver
    if opts:
        spec["driver_opts"] = opts
    return spec


# (label, normalized top-level volume, verdict, text the refusal must name)
VOLUME_CASES = [
    ("bind idiom", _volume("local", type="none", o="bind", device="/srv/a"), BIND, ""),
    ("bind idiom without driver", _volume(type="none", o="bind", device="/srv/a"), BIND, ""),
    ("bind read-only", _volume("local", type="none", o="bind,ro", device="/srv/a"), BIND, ""),
    ("bind read-write", _volume("local", type="none", o="bind,rw", device="/srv/a"), BIND, ""),
    ("recursive bind", _volume("local", type="none", o="rbind", device="/srv/a"), BIND, ""),
    ("plain named volume", {}, PLAIN, ""),
    ("plain with local driver", _volume("local"), PLAIN, ""),
    ("nfs", _volume("local", type="nfs", o="addr=10.0.0.1", device=":/export"), REFUSED, "type"),
    ("tmpfs", _volume("local", type="tmpfs", o="size=1m", device="tmpfs"), REFUSED, "type"),
    ("relative device", _volume("local", type="none", o="bind", device="./data"), REFUSED, "device"),
    ("missing device", _volume("local", type="none", o="bind"), REFUSED, "device"),
    ("extra bind token", _volume("local", type="none", o="bind,uid=1", device="/srv/a"), REFUSED, "uid=1"),
    ("no bind token", _volume("local", type="none", o="ro", device="/srv/a"), REFUSED, "bind"),
    ("ro with rw", _volume("local", type="none", o="bind,ro,rw", device="/srv/a"), REFUSED, "ro"),
    ("other driver", _volume("other", type="none", o="bind", device="/srv/a"), REFUSED, "driver"),
    ("extra driver option", _volume("local", type="none", o="bind", device="/srv/a", size="1g"), REFUSED, "size"),
]

# (label, network driver_opts, options not applied, options refused)
NETWORK_CASES = [
    ("none", None, [], []),
    ("empty", {}, [], []),
    ("bridge name only", {BRIDGE: "br0"}, [BRIDGE], []),
    ("bridge name and mtu", {BRIDGE: "br0", MTU: "1400"}, [BRIDGE], [MTU]),
    ("mtu only", {MTU: "1400"}, [], [MTU]),
]


def _compat_volume(spec: dict) -> tuple[str, str]:
    """Surface 1: the pure module's verdict for a volume declaration."""
    result = compat.local_bind(spec)
    if isinstance(result, compat.LocalBind):
        return BIND, ""
    if isinstance(result, compat.Problem):
        return REFUSED, result.message
    return PLAIN, ""


def _compat_network(opts) -> tuple[list, list]:
    """Surface 1: the pure module's split of a network's driver_opts."""
    not_applied, refused = compat.classify_network_options(opts)
    return (
        [key for key in (opts or {}) if any(key in text for text in not_applied)],
        [key for key in (opts or {}) if any(key in text for text in refused)],
    )


def _gate_report(kind: str, spec: dict):
    """The gate's report for one resource used by one service."""
    if kind == "volumes":
        service = {"image": "x", "volumes": [{"type": "volume", "source": "res", "target": "/d"}]}
    else:
        service = {"image": "x", "networks": ["res"]}
    return caps.validate({kind: {"res": spec}}, [("svc", service)], environ={})


def _gate_volume(spec: dict) -> tuple[str, str]:
    """Surface 2: the capability gate's verdict for a volume declaration."""
    report = _gate_report("volumes", spec)
    if report.refusals:
        return REFUSED, " ".join(finding.message for finding in report.refusals)
    return ACCEPTED, ""


def _translator_volume(spec: dict) -> tuple[str, str]:
    """Surface 3: the translator's mount for a service using the volume."""
    project = compose.Project(
        "p", {"services": {"svc": {"image": "x"}}, "volumes": {"res": spec}}, "/tmp/p"
    )
    try:
        planned = compose._entry_mount("res:/d", project)
    except mounts.MountSpecError as exc:
        return REFUSED, str(exc)
    return (BIND if planned.mount.kind == "bind" else PLAIN), ""


def _gate_network(opts) -> tuple[list, list]:
    """Surface 2: the capability gate's split of a network's driver_opts."""
    spec = {} if opts is None else {"driver_opts": opts}
    report = _gate_report("networks", spec)
    keys = list(opts or {})
    return (
        [key for key in keys if any(key in item.message for item in report.notices)],
        [key for key in keys if any(key in item.message for item in report.refusals)],
    )


# name -> (probe, coarse): a coarse surface cannot tell BIND from PLAIN.
VOLUME_SURFACES = {
    "compose_resource_compat": (_compat_volume, False),
    "compose_capabilities": (_gate_volume, True),
    "compose._entry_mount": (_translator_volume, False),
}
NETWORK_SURFACES = {
    "compose_resource_compat": _compat_network,
    "compose_capabilities": _gate_network,
}


def disagreements(
    volume_cases=None, network_cases=None, volume_surfaces=None, network_surfaces=None
) -> list[str]:
    """Mismatches between the tables and the surfaces (empty when all agree)."""
    volume_cases = VOLUME_CASES if volume_cases is None else volume_cases
    network_cases = NETWORK_CASES if network_cases is None else network_cases
    volume_surfaces = VOLUME_SURFACES if volume_surfaces is None else volume_surfaces
    network_surfaces = NETWORK_SURFACES if network_surfaces is None else network_surfaces
    found: list[str] = []
    for label, spec, verdict, names in volume_cases:
        for surface, (probe, coarse) in volume_surfaces.items():
            got, message = probe(spec)
            expected = ACCEPTED if coarse and verdict != REFUSED else verdict
            if got != expected:
                found.append(f"{surface}: volume {label!r}: expected {expected}, got {got}")
            elif verdict == REFUSED and names not in message:
                found.append(f"{surface}: volume {label!r}: refusal does not name {names!r}: {message}")
    for label, opts, not_applied, refused in network_cases:
        for surface, probe in network_surfaces.items():
            got = probe(opts)
            if got != (not_applied, refused):
                found.append(
                    f"{surface}: network {label!r}: expected {(not_applied, refused)}, got {got}"
                )
    return found


class ResourceOptionContractTests(unittest.TestCase):
    def test_every_surface_agrees_with_the_tables(self):
        self.assertEqual(disagreements(), [])

    def test_the_tables_cover_every_verdict(self):
        self.assertEqual({case[2] for case in VOLUME_CASES}, {BIND, PLAIN, REFUSED})
        self.assertTrue(any(case[2] for case in NETWORK_CASES))
        self.assertTrue(any(case[3] for case in NETWORK_CASES))

    def test_a_wrong_table_entry_is_detected(self):
        wrong = [(label, spec, PLAIN if verdict == BIND else verdict, names)
                 for label, spec, verdict, names in VOLUME_CASES]
        self.assertTrue(disagreements(volume_cases=wrong))

    def test_a_wrong_surface_is_detected(self):
        def always_bind(spec):
            return BIND, ""

        self.assertTrue(disagreements(volume_surfaces={"patched": (always_bind, False)}))

        def never_refuse(opts):
            return [], []

        self.assertTrue(disagreements(network_surfaces={"patched": never_refuse}))

    def test_a_refusal_that_does_not_name_the_option_is_detected(self):
        def anonymous_refusal(spec):
            got, _ = _compat_volume(spec)
            return got, "refused"

        found = disagreements(volume_surfaces={"patched": (anonymous_refusal, False)})
        self.assertTrue(any("does not name" in item for item in found))


if __name__ == "__main__":
    unittest.main()
