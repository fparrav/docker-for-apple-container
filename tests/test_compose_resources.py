from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim import compose_resources  # noqa: E402

TIER = "unit"


class FakeProject:
    """Duck-typed stand-in for the parsed model: name + networks + volumes + services."""

    def __init__(self, name, networks=None, volumes=None, services=None):
        self.name = name
        self.networks = networks
        self.volumes = volumes
        self.services = services


class ConstantsTests(unittest.TestCase):
    def test_label_names_match_compose(self):
        self.assertEqual(compose_resources.LABEL_PROJECT, "com.docker.compose.project")
        self.assertEqual(compose_resources.LABEL_NETWORK, "com.docker.compose.network")
        self.assertEqual(compose_resources.LABEL_VOLUME, "com.docker.compose.volume")
        self.assertEqual(compose_resources.LABEL_SERVICE, "com.docker.compose.service")
        self.assertEqual(compose_resources.LABEL_ANONYMOUS, "com.docker.compose.anonymous")


class NameResolutionTests(unittest.TestCase):
    def test_managed_resource_takes_project_prefix(self):
        result = compose_resources.resolve(FakeProject("demo", networks={"back": {}}, volumes={"data": {}}))
        self.assertEqual(result.networks["back"].name, "demo_back")
        self.assertEqual(result.volumes["data"].name, "demo_data")
        self.assertTrue(result.volumes["data"].managed)
        self.assertFalse(result.volumes["data"].external)

    def test_explicit_name_is_used_without_prefix(self):
        result = compose_resources.resolve(FakeProject("demo", volumes={"data": {"name": "pgdata-prod"}}))
        volume = result.volumes["data"]
        self.assertEqual(volume.name, "pgdata-prod")
        self.assertTrue(volume.managed)

    def test_external_without_name_keeps_the_key(self):
        result = compose_resources.resolve(FakeProject("demo", volumes={"shared_data": {"external": True}}))
        volume = result.volumes["shared_data"]
        self.assertEqual(volume.name, "shared_data")
        self.assertTrue(volume.external)
        self.assertFalse(volume.managed)

    def test_external_with_name_uses_the_name(self):
        result = compose_resources.resolve(FakeProject("demo", networks={"corp": {"external": True, "name": "corp-net"}}))
        self.assertEqual(result.networks["corp"].name, "corp-net")
        self.assertTrue(result.networks["corp"].external)

    def test_legacy_external_mapping_contributes_the_name(self):
        result = compose_resources.resolve(FakeProject("demo", networks={"corp": {"external": {"name": "corp-net"}}}))
        net = result.networks["corp"]
        self.assertEqual(net.name, "corp-net")
        self.assertTrue(net.external)
        self.assertFalse(net.managed)

    def test_legacy_external_mapping_without_name_falls_back_to_key(self):
        result = compose_resources.resolve(FakeProject("demo", volumes={"shared": {"external": {}}}))
        volume = result.volumes["shared"]
        self.assertEqual(volume.name, "shared")
        self.assertTrue(volume.external)

    def test_explicit_name_wins_over_external(self):
        result = compose_resources.resolve(FakeProject("demo", volumes={"v": {"external": True, "name": "kept"}}))
        self.assertEqual(result.volumes["v"].name, "kept")


class DefaultNetworkTests(unittest.TestCase):
    def test_default_network_is_added_when_absent(self):
        for networks in (None, {}):
            with self.subTest(networks=networks):
                result = compose_resources.resolve(FakeProject("demo", networks=networks))
                self.assertEqual(result.networks["default"].name, "demo_default")
                self.assertTrue(result.networks["default"].managed)

    def test_declared_default_network_is_resolved_like_any_other(self):
        result = compose_resources.resolve(FakeProject("demo", networks={"default": {}}))
        self.assertEqual(result.networks["default"].name, "demo_default")
        self.assertTrue(result.networks["default"].managed)

    def test_declared_default_network_with_name_keeps_one_entry(self):
        result = compose_resources.resolve(FakeProject("demo", networks={"default": {"name": "net"}}))
        self.assertEqual(result.networks, {"default": result.networks["default"]})
        self.assertEqual(result.networks["default"].name, "net")


class NoneAndEquivalenceTests(unittest.TestCase):
    def test_none_maps_and_none_specs_are_accepted(self):
        result = compose_resources.resolve(FakeProject("demo", networks=None, volumes=None))
        self.assertEqual(result.project, "demo")
        self.assertEqual(result.networks["default"].name, "demo_default")
        self.assertEqual(result.volumes, {})
        result = compose_resources.resolve(FakeProject("demo", networks={"a": None}, volumes={"b": None}))
        self.assertEqual(result.networks["a"].name, "demo_a")
        self.assertEqual(result.volumes["b"].name, "demo_b")

    def test_official_and_builtin_models_resolve_identically(self):
        official = FakeProject(
            "demo",
            networks={"default": {"name": "demo_default"}, "corp": {"name": "corp", "external": True}},
            volumes={"data": {"name": "demo_data"}, "shared": {"name": "shared", "external": True},
                     "pg": {"name": "pgdata-prod"}},
        )
        builtin = FakeProject(
            "demo",
            networks={"default": {}, "corp": {"external": True}},
            volumes={"data": {}, "shared": {"external": True}, "pg": {"name": "pgdata-prod"}},
        )
        self.assertEqual(compose_resources.resolve(official), compose_resources.resolve(builtin))


class LabelsTests(unittest.TestCase):
    def test_dict_labels_are_normalized(self):
        result = compose_resources.resolve(FakeProject("demo", volumes={"data": {"labels": {"a": "1", "b": "2"}}}))
        self.assertEqual(result.volumes["data"].labels, {"a": "1", "b": "2"})

    def test_list_labels_are_normalized_to_the_same_dict(self):
        as_list = compose_resources.resolve(FakeProject("demo", volumes={"data": {"labels": ["a=1", "b=2"]}}))
        as_dict = compose_resources.resolve(FakeProject("demo", volumes={"data": {"labels": {"a": "1", "b": "2"}}}))
        self.assertEqual(as_list.volumes["data"].labels, as_dict.volumes["data"].labels)

    def test_missing_or_empty_labels_yield_an_empty_dict(self):
        result = compose_resources.resolve(FakeProject("demo", volumes={"a": None, "b": {"labels": []}}))
        self.assertEqual(result.volumes["a"].labels, {})
        self.assertEqual(result.volumes["b"].labels, {})


ANONYMOUS_SERVICES = {
    "api": {
        "volumes": [
            "/app/node_modules",  # short syntax
            {"type": "volume", "target": "/cache"},  # long syntax
            {"type": "volume", "target": "/cache"},  # duplicate entry
            "./src:/app",  # bind (short)
            {"type": "bind", "source": "./src", "target": "/app"},  # bind (long)
            {"type": "tmpfs", "target": "/tmp"},
            {"type": "volume", "source": "data", "target": "/data"},  # named
            {"type": "image", "source": "img", "target": "/x"},
        ]
    },
    "worker": {
        "volumes": [
            "/app/node_modules",  # same target as api's, different service
            {"target": "/scratch"},  # type absent
        ]
    },
}


def _anon_name(project, service, target):
    return f"{project}_{service}_anon-{hashlib.sha256(target.encode()).hexdigest()[:10]}"


class AnonymousVolumeTests(unittest.TestCase):
    def test_same_inputs_give_the_same_name(self):
        first = compose_resources.anonymous_volume("demo", "api", "/app/node_modules")
        second = compose_resources.anonymous_volume("demo", "api", "/app/node_modules")
        self.assertEqual(first, second)

    def test_different_target_service_or_project_give_different_names(self):
        base = compose_resources.anonymous_volume("demo", "api", "/app/node_modules")
        self.assertNotEqual(
            base.name, compose_resources.anonymous_volume("demo", "api", "/app/site-packages").name
        )
        self.assertNotEqual(
            base.name, compose_resources.anonymous_volume("demo", "worker", "/app/node_modules").name
        )
        self.assertNotEqual(
            base.name, compose_resources.anonymous_volume("prod", "api", "/app/node_modules").name
        )

    def test_name_is_the_hashed_target(self):
        resource = compose_resources.anonymous_volume("demo", "api", "/app/node_modules")
        self.assertEqual(resource.name, _anon_name("demo", "api", "/app/node_modules"))

    def test_name_is_a_valid_runtime_name_for_dotted_and_dashed_services(self):
        resource = compose_resources.anonymous_volume("demo", "api.v1-2", "/cache")
        self.assertRegex(resource.name, r"^[A-Za-z0-9_.-]+$")

    def test_labels_carry_project_service_and_target(self):
        resource = compose_resources.anonymous_volume("demo", "api", "/app/node_modules")
        self.assertEqual(
            resource.labels,
            {
                "com.docker.compose.project": "demo",
                "com.docker.compose.service": "api",
                "com.docker.compose.anonymous": "/app/node_modules",
            },
        )
        self.assertEqual(resource.key, "/app/node_modules")
        self.assertFalse(resource.external)
        self.assertTrue(resource.managed)


class ResolveAnonymousTests(unittest.TestCase):
    def test_resolve_lists_anonymous_volumes_in_service_then_entry_order(self):
        result = compose_resources.resolve(FakeProject("demo", services=ANONYMOUS_SERVICES))
        self.assertEqual(
            [r.name for r in result.anonymous],
            [
                _anon_name("demo", "api", "/app/node_modules"),
                _anon_name("demo", "api", "/cache"),
                _anon_name("demo", "worker", "/app/node_modules"),
                _anon_name("demo", "worker", "/scratch"),
            ],
        )
        self.assertEqual(
            [r.key for r in result.anonymous],
            ["/app/node_modules", "/cache", "/app/node_modules", "/scratch"],
        )

    def test_project_without_services_attribute_resolves_as_before(self):
        project = FakeProject("demo", networks={"back": {}}, volumes={"data": {}})
        del project.services
        result = compose_resources.resolve(project)
        self.assertEqual(result.anonymous, ())
        self.assertEqual(result.networks["back"].name, "demo_back")
        self.assertEqual(result.volumes["data"].name, "demo_data")
        self.assertEqual(result.networks["default"].name, "demo_default")

    def test_missing_or_empty_services_yield_no_anonymous(self):
        for services in (None, {}):
            with self.subTest(services=services):
                result = compose_resources.resolve(
                    FakeProject("demo", volumes={"data": {}}, services=services)
                )
                self.assertEqual(result.anonymous, ())
                self.assertEqual(result.volumes["data"].name, "demo_data")

    def test_duplicate_entries_collapse_to_one(self):
        services = {"api": {"volumes": ["/cache", "/cache", "/cache"]}}
        result = compose_resources.resolve(FakeProject("demo", services=services))
        self.assertEqual([r.name for r in result.anonymous], [_anon_name("demo", "api", "/cache")])


class RemovableTests(unittest.TestCase):
    def test_only_owned_managed_resources_survive_in_order(self):
        result = compose_resources.resolve(FakeProject(
            "demo",
            networks={"back": {}, "front": {}},
            volumes={"data": {}, "cache": {}, "shared": {"external": True}},
        ))
        labelled = {
            "demo_data": "demo",     # managed, ours                -> kept
            "demo_cache": "other",   # managed, another project     -> excluded
            "demo_front": "",        # managed, empty label         -> excluded
            "shared": "demo",        # external                     -> excluded
            # demo_back is absent from `labelled`                   -> excluded
        }
        kept = compose_resources.removable(
            [*result.networks.values(), *result.volumes.values()], labelled, "demo"
        )
        self.assertEqual([r.name for r in kept], ["demo_data"])

    def test_managed_with_matching_label_is_kept_and_order_is_preserved(self):
        result = compose_resources.resolve(FakeProject(
            "demo",
            networks={"a": {}, "b": {}},
            volumes={"c": {}, "d": {}},
        ))
        labelled = {
            "demo_b": "demo",
            "demo_a": "demo",
            "demo_d": "demo",
            "demo_c": "other",
        }
        kept = compose_resources.removable(
            [*result.networks.values(), *result.volumes.values()], labelled, "demo"
        )
        self.assertEqual([r.name for r in kept], ["demo_a", "demo_b", "demo_d"])

    def test_external_and_unlabelled_are_excluded_even_when_labelled(self):
        result = compose_resources.resolve(FakeProject(
            "demo", networks={"net": {}}, volumes={"v": {"external": True}}
        ))
        labelled = {"demo_net": "demo", "v": "demo"}
        kept = compose_resources.removable(
            [*result.networks.values(), *result.volumes.values()], labelled, "demo"
        )
        self.assertEqual([r.name for r in kept], ["demo_net"])

    def test_no_labelled_entries_yields_nothing(self):
        result = compose_resources.resolve(FakeProject("demo", volumes={"data": {}}))
        kept = compose_resources.removable(result.volumes.values(), {}, "demo")
        self.assertEqual(kept, [])

    def test_removable_keeps_only_own_labelled_anonymous_volumes(self):
        result = compose_resources.resolve(FakeProject("demo", services=ANONYMOUS_SERVICES))
        api_modules = _anon_name("demo", "api", "/app/node_modules")
        api_cache = _anon_name("demo", "api", "/cache")
        labelled = {
            api_modules: "demo",   # managed, ours          -> kept
            api_cache: "other",    # managed, other project -> excluded
            # the worker volumes are unlabelled            -> excluded
        }
        kept = compose_resources.removable(result.anonymous, labelled, "demo")
        self.assertEqual([r.name for r in kept], [api_modules])


if __name__ == "__main__":
    unittest.main()
