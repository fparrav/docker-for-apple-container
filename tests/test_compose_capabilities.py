from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim import compose_capabilities as caps  # noqa: E402
from container_docker_shim import security  # noqa: E402

TIER = "unit"

NORMALIZED = ROOT / "tests" / "fixtures" / "compose" / "normalized"


def run(services, project=None, env=None):
    return caps.validate(project or {}, list(services.items()) if isinstance(services, dict) else services, env or {})


def keys(findings):
    return [(f.service, f.key) for f in findings]


class ClassificationTests(unittest.TestCase):
    def test_supported_keys_pass_silently(self):
        report = run({"web": {"image": "nginx", "ports": ["80:80"], "environment": {"A": "1"}, "read_only": True}})
        self.assertEqual(report.refusals, [])
        self.assertEqual(report.notices, [])

    def test_unknown_key_is_refused_with_service_and_key(self):
        report = run({"web": {"image": "x", "brand_new_key": 1}})
        self.assertEqual(keys(report.refusals), [("web", "brand_new_key")])

    def test_extension_keys_are_ignored(self):
        report = run({"web": {"image": "x", "x-owner": "team-a"}})
        self.assertTrue(report.ok)
        self.assertEqual(report.notices, [])

    def test_named_restriction_keys_in_the_spec_are_classified(self):
        for key in (
            "pid", "ipc", "uts", "cgroup_parent", "userns_mode", "cpu_shares",
            "cpu_quota", "mem_reservation", "memswap_limit", "oom_score_adj",
            "group_add", "device_cgroup_rules", "runtime", "gpus", "volumes_from",
        ):
            with self.subTest(key=key):
                self.assertIn(key, caps.SERVICE_KEYS)
                rule = caps.SERVICE_KEYS[key]
                self.assertEqual((rule.status, rule.effect), (caps.UNSUPPORTED, caps.RESTRICT))

    def test_tables_declare_valid_statuses(self):
        tables = [caps.SERVICE_KEYS, caps.BUILD_KEYS, caps.DEPLOY_KEYS]
        tables.extend(caps.RESOURCE_ATTRIBUTES.values())
        for table in tables:
            for name, rule in table.items():
                with self.subTest(name=name):
                    self.assertIn(rule.status, (caps.SUPPORTED, caps.PARTIAL, caps.UNSUPPORTED))
                    self.assertIn(rule.effect, (caps.RESTRICT, caps.BEHAVIOUR, caps.INFORM))
                    self.assertTrue(rule.reason)

    def test_every_key_of_the_recorded_normalized_models_is_classified(self):
        seen = set()
        for path in sorted(NORMALIZED.glob("*.json")):
            model = json.loads(path.read_text())
            for service in model["services"].values():
                seen.update(service)
        self.assertTrue(seen)
        missing = sorted(k for k in seen if k not in caps.SERVICE_KEYS and not k.startswith("x-"))
        self.assertEqual(missing, [])

    def test_recorded_models_have_no_unclassified_subkeys(self):
        # Build sub-keys, mount options and network/volume attributes of the
        # recorded models must also be known to the tables.
        for path in sorted(NORMALIZED.glob("*.json")):
            model = json.loads(path.read_text())
            report = caps.validate(model, list(model["services"].items()), {})
            unknown = [f for f in report.refusals if "unknown" in f.message]
            self.assertEqual(unknown, [], path.name)

    def test_module_depends_only_on_security_and_mounts(self):
        source = (ROOT / "src" / "container_docker_shim" / "compose_capabilities.py").read_text()
        imports = re.findall(r"^from \. import (\w+)|^from \.(\w+) import", source, re.M)
        names = {a or b for a, b in imports}
        self.assertEqual(names, {"security", "mounts"})


class IsolationTests(unittest.TestCase):
    def test_network_mode_values_are_refused(self):
        for value in ("none", "host", "service:db", "container:x", "bridge"):
            with self.subTest(value=value):
                report = run({"worker": {"image": "x", "network_mode": value}})
                self.assertEqual(keys(report.refusals), [("worker", "network_mode")])

    def test_waiver_has_no_effect_on_network_mode(self):
        env = {security.ENV_ALLOW: "network_mode,network-none"}
        report = run({"worker": {"image": "x", "network_mode": "none"}}, env=env)
        self.assertEqual(keys(report.refusals), [("worker", "network_mode")])
        self.assertIn("cannot be waived", report.refusals[0].message)
        self.assertEqual(report.notices, [])

    def test_secrets_and_configs_are_refused_and_not_waivable(self):
        env = {security.ENV_ALLOW: "secrets,configs"}
        report = run({"db": {"image": "x", "secrets": ["pw"], "configs": ["c"]}}, env=env)
        self.assertEqual(keys(report.refusals), [("db", "secrets"), ("db", "configs")])
        self.assertIn("Compose", report.refusals[0].message)
        self.assertNotIn("Swarm", report.refusals[0].message)

    def test_all_problems_are_reported_together(self):
        report = run({
            "a": {"image": "x", "network_mode": "none"},
            "b": {"image": "x", "secrets": ["pw"]},
        })
        self.assertEqual(keys(report.refusals), [("a", "network_mode"), ("b", "secrets")])


class RestrictionTests(unittest.TestCase):
    def test_restriction_without_equivalent_is_refused(self):
        report = run({"w": {"image": "x", "mem_reservation": "256m"}})
        self.assertEqual(keys(report.refusals), [("w", "mem_reservation")])
        self.assertIn("mem_reservation", report.refusals[0].message)

    def test_explicit_waiver_turns_the_refusal_into_a_notice(self):
        env = {security.ENV_ALLOW: "mem_reservation"}
        report = run({"w": {"image": "x", "mem_reservation": "256m"}}, env=env)
        self.assertEqual(report.refusals, [])
        self.assertEqual(keys(report.notices), [("w", "mem_reservation")])

    def test_security_keys_follow_security_decide(self):
        refused = run({"w": {"image": "x", "privileged": True}})
        self.assertEqual(keys(refused.refusals), [("w", "privileged")])
        waived = run({"w": {"image": "x", "privileged": True}}, env={security.ENV_ALLOW: "privileged"})
        self.assertEqual(waived.refusals, [])
        self.assertEqual(keys(waived.notices), [("w", "privileged")])
        self.assertTrue(run({"w": {"image": "x", "privileged": False}}).ok)

    def test_empty_restriction_values_are_not_requests(self):
        self.assertTrue(run({"w": {"image": "x", "group_add": [], "pid": ""}}).ok)


class BehaviourTests(unittest.TestCase):
    def test_restart_policy_is_a_notice(self):
        report = run({"w": {"image": "x", "restart": "always"}})
        self.assertTrue(report.ok)
        self.assertEqual(keys(report.notices), [("w", "restart")])

    def test_default_values_are_silent(self):
        report = run({"w": {"image": "x", "restart": "no", "deploy": {"replicas": 1}, "scale": 1}})
        self.assertEqual((report.refusals, report.notices), ([], []))

    def test_replicas_above_one_is_a_notice(self):
        report = run({"w": {"image": "x", "deploy": {"replicas": 3}}})
        self.assertEqual(keys(report.notices), [("w", "deploy.replicas")])

    def test_partial_keys_from_the_spec(self):
        for key, value in (
            ("hostname", "h"), ("domainname", "d"), ("extra_hosts", ["a:1.1.1.1"]),
            ("stop_signal", "SIGINT"), ("stop_grace_period", "5s"), ("mac_address", "02:42:ac:11:00:02"),
            ("links", ["db"]), ("logging", {"driver": "json-file"}),
        ):
            with self.subTest(key=key):
                report = run({"w": {"image": "x", key: value}})
                self.assertEqual(keys(report.notices), [("w", key)])
                self.assertTrue(report.ok)

    def test_network_aliases_are_a_notice(self):
        report = run({"w": {"image": "x", "networks": {"default": {"aliases": ["api"]}}}})
        self.assertEqual(keys(report.notices), [("w", "networks.aliases")])


class BuildTests(unittest.TestCase):
    def test_forwarded_build_keys_pass(self):
        build = {"context": ".", "dockerfile": "D", "args": {"A": "1"}, "target": "runtime", "labels": {"a": "b"},
                 "platforms": ["linux/arm64"], "no_cache": True, "pull": True, "ssh": ["default"]}
        self.assertTrue(run({"w": {"build": build}}).ok)

    def test_unsupported_build_key_is_refused(self):
        report = run({"w": {"build": {"context": ".", "cache_from": ["x"]}}})
        self.assertEqual(keys(report.refusals), [("w", "build.cache_from")])

    def test_unknown_build_key_is_refused(self):
        report = run({"w": {"build": {"context": ".", "future": 1}}})
        self.assertEqual(keys(report.refusals), [("w", "build.future")])

    def test_ssh_other_than_default_is_refused(self):
        report = run({"w": {"build": {"context": ".", "ssh": ["mykey=/k"]}}})
        self.assertEqual(keys(report.refusals), [("w", "build.ssh")])


class VolumeTests(unittest.TestCase):
    def test_modes_that_are_accepted(self):
        for mode in ("ro", "rw", "cached", "delegated"):
            with self.subTest(mode=mode):
                self.assertTrue(run({"w": {"image": "x", "volumes": ["data:/d:" + mode]}}).ok)

    def test_selinux_modes_are_refused_with_the_selinux_reason(self):
        for mode, key in (("z", "volumes.z"), ("Z", "volumes.Z"), ("ro,z", "volumes.z"), ("Z,ro", "volumes.Z")):
            with self.subTest(mode=mode):
                report = run({"w": {"image": "x", "volumes": ["data:/d:" + mode]}})
                self.assertEqual(keys(report.refusals), [("w", key)])
                self.assertIn("SELinux", report.refusals[0].message)
                self.assertIn("/d", report.refusals[0].message)
                self.assertEqual(report.notices, [])

    def test_long_syntax_selinux_is_refused(self):
        entry = {"type": "bind", "source": "/h", "target": "/t", "bind": {"selinux": "z"}}
        report = run({"w": {"image": "x", "volumes": [entry]}})
        self.assertEqual(keys(report.refusals), [("w", "volumes.selinux")])
        self.assertIn("SELinux", report.refusals[0].message)
        self.assertIn("/t", report.refusals[0].message)

    def test_named_volume_with_selinux_is_refused(self):
        # The shape the normalizer emits for `data:/d:Z`.
        entry = {"type": "volume", "source": "data", "target": "/d", "bind": {"selinux": "Z"}, "volume": {}}
        report = run({"w": {"image": "x", "volumes": [entry]}})
        self.assertEqual(keys(report.refusals), [("w", "volumes.selinux")])
        self.assertIn("SELinux", report.refusals[0].message)
        self.assertIn("/d", report.refusals[0].message)

    def test_empty_or_absent_selinux_is_accepted(self):
        for entry in (
            {"type": "volume", "source": "data", "target": "/d", "bind": {"selinux": ""}, "volume": {}},
            {"type": "volume", "source": "data", "target": "/d", "volume": {}},
        ):
            with self.subTest(entry=entry):
                self.assertTrue(run({"w": {"image": "x", "volumes": [entry]}}).ok)

    def test_unknown_nested_key_is_refused(self):
        entry = {"type": "bind", "source": "/h", "target": "/t", "bind": {"weird": 1}}
        report = run({"w": {"image": "x", "volumes": [entry]}})
        self.assertEqual(keys(report.refusals), [("w", "volumes.weird")])
        self.assertIn("/t", report.refusals[0].message)

    def test_refusals_are_not_waived_by_the_allow_list(self):
        names = [
            key
            for key, rule in caps.SERVICE_KEYS.items()
            if rule.status == caps.UNSUPPORTED and rule.waivable and key not in caps.SECURITY_KEYS
        ]
        names += [
            name
            for name, rule in security.RULES.items()
            if rule.status == security.UNSUPPORTED and rule.waivable
        ]
        env = {security.ENV_ALLOW: ",".join(sorted(names))}
        self.assertEqual(caps.unknown_waivers(env), [])
        for volumes in (
            ["data:/d:z"],
            ["data:/d:ro,z"],
            [{"type": "volume", "source": "data", "target": "/d", "bind": {"selinux": "Z"}, "volume": {}}],
            [{"type": "bind", "source": "/h", "target": "/t", "bind": {"propagation": "shared"}}],
        ):
            with self.subTest(volumes=volumes):
                report = run({"w": {"image": "x", "volumes": volumes}}, env=env)
                self.assertTrue(report.refusals)
                self.assertEqual(report.notices, [])

    def test_unsupported_short_option_is_refused(self):
        report = run({"w": {"image": "x", "volumes": ["data:/d:nocopy"]}})
        self.assertEqual(keys(report.refusals), [("w", "volumes.nocopy")])

    def test_unknown_short_option_is_refused(self):
        report = run({"w": {"image": "x", "volumes": ["data:/d:weird"]}})
        self.assertEqual(keys(report.refusals), [("w", "volumes.weird")])

    def test_long_syntax_read_only_bind_is_accepted(self):
        entry = {"type": "bind", "source": "/h", "target": "/t", "read_only": True, "bind": {"create_host_path": True}}
        self.assertTrue(run({"w": {"image": "x", "volumes": [entry]}}).ok)

    def test_consistency_is_accepted_without_effect(self):
        entry = {"type": "volume", "source": "data", "target": "/d", "consistency": "delegated"}
        report = run({"w": {"image": "x", "volumes": [entry]}})
        self.assertTrue(report.ok)

    def test_image_mount_type_is_refused(self):
        report = run({"w": {"image": "x", "volumes": [{"type": "image", "source": "img", "target": "/t"}]}})
        self.assertEqual(keys(report.refusals), [("w", "volumes.image")])
        self.assertIn("/t", report.refusals[0].message)

    def test_tmpfs_mount_type_is_refused(self):
        report = run({"w": {"image": "x", "volumes": [{"type": "tmpfs", "target": "/t"}]}})
        self.assertEqual(keys(report.refusals), [("w", "volumes.tmpfs")])

    def test_bind_propagation_is_refused(self):
        entry = {"type": "bind", "source": "/h", "target": "/t", "bind": {"propagation": "shared"}}
        report = run({"w": {"image": "x", "volumes": [entry]}})
        self.assertEqual(keys(report.refusals), [("w", "volumes.propagation")])

    def test_refusal_names_the_mount_target(self):
        short = run({"w": {"image": "x", "volumes": ["data:/short:nocopy"]}})
        entry = {"type": "volume", "source": "data", "target": "/long", "volume": {"nocopy": True}}
        long = run({"w": {"image": "x", "volumes": [entry]}})
        self.assertIn("/short", short.refusals[0].message)
        self.assertIn("/long", long.refusals[0].message)


class ResourceTests(unittest.TestCase):
    def test_volume_driver_opts_are_refused_for_referenced_volumes(self):
        project = {"volumes": {"data": {"driver_opts": {"type": "nfs"}}}}
        report = run({"w": {"image": "x", "volumes": ["data:/d"]}}, project)
        self.assertEqual(keys(report.refusals), [("w", "volumes.driver_opts")])

    def test_unreferenced_resources_are_not_validated(self):
        project = {"volumes": {"other": {"driver_opts": {"type": "nfs"}}}}
        self.assertTrue(run({"w": {"image": "x", "volumes": ["data:/d"]}}, project).ok)

    def test_network_attributes(self):
        project = {"networks": {"back": {"internal": True, "labels": {"a": "b"}, "ipam": {"driver": "x"}}}}
        report = run({"w": {"image": "x", "networks": ["back"]}}, project)
        self.assertEqual(keys(report.refusals), [("w", "networks.ipam")])

    def test_internal_and_labels_are_honoured(self):
        project = {"networks": {"back": {"internal": True, "labels": {"a": "b"}, "name": "p_back"}}}
        self.assertTrue(run({"w": {"image": "x", "networks": ["back"]}}, project).ok)

    def test_non_default_driver_is_refused(self):
        project = {"networks": {"default": {"driver": "overlay"}}}
        report = run({"w": {"image": "x"}}, project)
        self.assertEqual(keys(report.refusals), [("w", "networks.driver")])


class UnknownWaiverTests(unittest.TestCase):
    def test_compose_key_waivers_are_known(self):
        env = {security.ENV_ALLOW: "mem_reservation,pids-limit"}
        self.assertEqual(caps.unknown_waivers(env), [])

    def test_typos_and_isolation_requests_are_reported(self):
        env = {security.ENV_ALLOW: "mem_reservaton,network_mode,secrets"}
        self.assertEqual(caps.unknown_waivers(env), ["mem_reservaton", "network_mode", "secrets"])


class SelectionTests(unittest.TestCase):
    def test_only_the_given_services_are_validated(self):
        services = {"api": {"image": "x"}, "legacy": {"image": "x", "network_mode": "host"}}
        report = caps.validate({}, [("api", services["api"])], {})
        self.assertTrue(report.ok)

    def test_service_objects_with_name_and_spec_are_accepted(self):
        class S:
            name = "db"
            spec = {"image": "x", "secrets": ["pw"]}

        report = caps.validate({}, [S()], {})
        self.assertEqual(keys(report.refusals), [("db", "secrets")])


class ResourceDriverTests(unittest.TestCase):
    def test_non_default_drivers_are_refused_and_default_ones_pass(self):
        services = {"w": {"image": "x", "volumes": ["data:/d"], "networks": ["n"]}}
        for kind, driver, ok in (
            ("networks", "overlay", False),
            ("networks", "bridge", True),
            ("volumes", "nfs", False),
            ("volumes", "local", True),
        ):
            with self.subTest(kind=kind, driver=driver):
                project = {"volumes": {"data": {}}, "networks": {"n": {}}}
                project[kind]["data" if kind == "volumes" else "n"] = {"driver": driver}
                report = run(services, project)
                self.assertEqual(report.ok, ok)
                if not ok:
                    self.assertIn(driver, report.refusals[0].message)


class SecretsTests(unittest.TestCase):
    SECRET_FILE = "/nonexistent/shimtest-secret-file"

    def project(self):
        return {"secrets": {"pw": {"file": self.SECRET_FILE}}}

    def test_the_secret_file_is_never_opened(self):
        import builtins

        real_open = builtins.open
        opened = []

        def guard(file, *args, **kwargs):
            opened.append(str(file))
            return real_open(file, *args, **kwargs)

        builtins.open = guard
        try:
            report = caps.validate(self.project(), [("db", {"image": "x", "secrets": ["pw"]})], {})
        finally:
            builtins.open = real_open
        self.assertEqual(keys(report.refusals), [("db", "secrets")])
        self.assertNotIn(self.SECRET_FILE, opened)

    def test_the_reason_calls_secrets_a_compose_feature(self):
        for key in ("secrets", "configs"):
            with self.subTest(key=key):
                message = run({"db": {"image": "x", key: ["a"]}}, self.project()).refusals[0].message
                self.assertIn("Compose %s" % key, message)
                self.assertNotIn("Swarm", message)

    def test_no_secret_value_reaches_the_report(self):
        report = run({"db": {"image": "x", "secrets": ["pw"]}}, {"secrets": {"pw": {"environment": "PW_VALUE_XYZ"}}})
        text = " ".join(f.message for f in report.refusals + report.notices)
        self.assertNotIn("PW_VALUE_XYZ", text)


if __name__ == "__main__":
    unittest.main()
