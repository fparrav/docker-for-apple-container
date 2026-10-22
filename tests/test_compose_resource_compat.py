from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim import compose_resource_compat  # noqa: E402

TIER = "unit"


def _bind_opts(**overrides) -> dict:
    """The local bind idiom, with one option overridden per case."""
    opts = {"type": "none", "o": "bind", "device": "/a"}
    opts.update(overrides)
    return opts


class LocalBindAcceptTests(unittest.TestCase):
    def test_bind_idiom_is_accepted(self):
        cases = [
            # (o, expected read_only)
            ("bind", False),
            ("bind,ro", True),
            ("bind,rw", False),
        ]
        for o, read_only in cases:
            with self.subTest(o=o):
                result = compose_resource_compat.local_bind(
                    {"driver_opts": dict(_bind_opts(o=o))}
                )
                self.assertIsInstance(result, compose_resource_compat.LocalBind)
                self.assertEqual(
                    (result.device, result.read_only),
                    ("/a", read_only),
                )

    def test_key_order_in_driver_opts_does_not_matter(self):
        result = compose_resource_compat.local_bind(
            {"driver_opts": {"device": "/a", "o": "bind", "type": "none"}}
        )
        self.assertIsInstance(result, compose_resource_compat.LocalBind)
        self.assertEqual(result.device, "/a")

    def test_local_driver_and_absent_driver_are_accepted(self):
        for driver in (None, "local"):
            spec = {"driver_opts": dict(_bind_opts())}
            if driver is not None:
                spec["driver"] = driver
            with self.subTest(driver=driver):
                result = compose_resource_compat.local_bind(spec)
                self.assertIsInstance(result, compose_resource_compat.LocalBind)


class LocalBindRefusalTests(unittest.TestCase):
    def _refused(self, spec: dict, option: str) -> str:
        result = compose_resource_compat.local_bind(spec)
        self.assertIsInstance(result, compose_resource_compat.Problem)
        message = result.message
        self.assertIn(option, message)
        return message

    def test_type_other_than_none_is_refused(self):
        for value in ("nfs", "tmpfs"):
            with self.subTest(value=value):
                self._refused({"driver_opts": _bind_opts(type=value)}, value)

    def test_missing_type_is_refused(self):
        opts = {"o": "bind", "device": "/a"}
        self._refused({"driver_opts": opts}, "type")

    def test_relative_device_is_refused_naming_the_path(self):
        self._refused({"driver_opts": _bind_opts(device="./data")}, "./data")

    def test_missing_device_is_refused(self):
        self._refused({"driver_opts": {"type": "none", "o": "bind"}}, "device")

    def test_o_tokens_outside_the_set_are_refused(self):
        for o, token in (("bind,uid=1", "uid=1"), ("bind,mode=777", "mode=777"), ("uid=1", "uid=1")):
            with self.subTest(o=o):
                self._refused({"driver_opts": _bind_opts(o=o)}, token)

    def test_rbind_is_refused_and_points_to_bind(self):
        for o in ("rbind", "rbind,ro"):
            with self.subTest(o=o):
                message = self._refused({"driver_opts": _bind_opts(o=o)}, "rbind")
                self.assertIn("use 'bind'", message)

    def test_o_without_bind_is_refused(self):
        for o in ("ro", "rw", ""):
            with self.subTest(o=o):
                self._refused({"driver_opts": _bind_opts(o=o)}, "o")

    def test_ro_with_rw_is_refused_naming_both(self):
        message = self._refused({"driver_opts": _bind_opts(o="bind,ro,rw")}, "ro")
        self.assertIn("rw", message)

    def test_driver_other_than_local_is_refused(self):
        self._refused(
            {
                "driver": "other",
                "driver_opts": dict(_bind_opts()),
            },
            "other",
        )
        # A non-local driver is refused even without driver_opts.
        self._refused({"driver": "nfs"}, "nfs")

    def test_extra_driver_opts_keys_are_refused(self):
        self._refused({"driver_opts": _bind_opts(addr="host")}, "addr")
        self._refused({"driver_opts": _bind_opts(foo="bar")}, "foo")

    def test_non_mapping_driver_opts_is_refused(self):
        self._refused({"driver_opts": ["type=none"]}, "driver_opts")


class NotABindTests(unittest.TestCase):
    def test_plain_named_volume_is_not_a_bind_and_not_a_problem(self):
        for spec in (
            {},
            None,
            {"driver": "local"},
            {"name": "demo_data"},
            {"labels": {"a": "b"}},
            {"driver_opts": {}},
        ):
            with self.subTest(spec=spec):
                result = compose_resource_compat.local_bind(spec)
                self.assertIs(result, compose_resource_compat.NOT_A_BIND)


class NetworkOptionsTests(unittest.TestCase):
    def test_bridge_name_alone_is_not_applied_only(self):
        not_applied, refused = compose_resource_compat.classify_network_options(
            {"com.docker.network.bridge.name": "tb-voxcom-br"}
        )
        self.assertEqual(len(not_applied), 1)
        self.assertIn("com.docker.network.bridge.name", not_applied[0])
        self.assertEqual(refused, [])

    def test_bridge_name_plus_another_option_is_refused_and_named(self):
        not_applied, refused = compose_resource_compat.classify_network_options(
            {
                "com.docker.network.bridge.name": "tb-voxcom-br",
                "com.docker.network.driver.mtu": "1500",
            }
        )
        self.assertEqual(len(not_applied), 1)
        self.assertEqual(len(refused), 1)
        self.assertIn("com.docker.network.driver.mtu", refused[0])

    def test_every_other_key_is_refused_and_named(self):
        _, refused = compose_resource_compat.classify_network_options(
            {
                "com.docker.network.driver.mtu": "1500",
                "com.docker.network.driver.enable_ipv6": "true",
            }
        )
        self.assertEqual(len(refused), 2)
        for key in ("com.docker.network.driver.mtu", "com.docker.network.driver.enable_ipv6"):
            self.assertTrue(any(key in message for message in refused))

    def test_empty_or_absent_opts_yield_nothing(self):
        for opts in (None, {}):
            with self.subTest(opts=opts):
                self.assertEqual(
                    compose_resource_compat.classify_network_options(opts), ([], [])
                )


class NetworkModeMessageTests(unittest.TestCase):
    def test_every_family_message_contains_the_value(self):
        for value in ("host", "none", "service:db", "container:x", "bridge", "default", "weird"):
            with self.subTest(value=value):
                self.assertIn(
                    value, compose_resource_compat.network_mode_message(value)
                )

    def test_host_message_mentions_host_docker_internal(self):
        message = compose_resource_compat.network_mode_message("host")
        self.assertIn("host.docker.internal", message)
        self.assertIn("no host network", message)

    def test_host_and_none_messages_differ(self):
        host = compose_resource_compat.network_mode_message("host")
        none = compose_resource_compat.network_mode_message("none")
        self.assertNotEqual(host, none)

    def test_service_and_container_values_get_distinct_messages(self):
        service = compose_resource_compat.network_mode_message("service:db")
        container = compose_resource_compat.network_mode_message("container:x")
        self.assertNotEqual(service, container)
        self.assertNotEqual(service, compose_resource_compat.network_mode_message("host"))


if __name__ == "__main__":
    unittest.main()
