"""A service with an empty `networks` uses the implicit `default` network.

`networks: []` and `networks: {}` mean the same as declaring no `networks`
(see ``compose_plan.service_network_keys``), so the capability validator must
register `default` for that service and check the top-level
`networks.default` declaration before anything is provisioned.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from container_docker_shim import compose_capabilities as caps  # noqa: E402
from container_docker_shim import security  # noqa: E402
import test_compose  # noqa: E402
from test_compose import ComposeFakeBase  # noqa: E402

TIER = "unit"

EMPTY_FORMS = (("list", []), ("mapping", {}))
EMPTY_YAML_FORMS = (("list", "[]"), ("mapping", "{}"))

UNSUPPORTED_DEFAULT_ATTRIBUTES = (
    ("ipam", {"ipam": {"driver": "x"}}),
    ("enable_ipv6", {"enable_ipv6": True}),
    ("driver_opts", {"driver_opts": {"com.docker.network.driver.mtu": "1500"}}),
    ("driver", {"driver": "overlay"}),
)


def run(services, project=None):
    return caps.validate(project or {}, list(services.items()), {})


def keys(findings):
    return [(f.service, f.key) for f in findings]


class EmptyNetworksValidateDefaultTests(unittest.TestCase):
    def test_unsupported_default_attributes_are_refused_for_empty_networks(self):
        for form, networks in EMPTY_FORMS:
            for attr, declaration in UNSUPPORTED_DEFAULT_ATTRIBUTES:
                with self.subTest(form=form, attr=attr):
                    project = {"networks": {"default": declaration}}
                    report = run({"w": {"image": "x", "networks": networks}}, project)
                    self.assertEqual(keys(report.refusals), [("w", "networks.%s" % attr)])
                    self.assertIn("default", report.refusals[0].message)

    def test_internal_and_labels_on_default_pass_for_empty_networks(self):
        project = {"networks": {"default": {"internal": True, "labels": {"team": "a"}}}}
        for form, networks in EMPTY_FORMS:
            with self.subTest(form=form):
                report = run({"w": {"image": "x", "networks": networks}}, project)
                self.assertEqual(report.refusals, [])
                self.assertEqual(report.notices, [])

    def test_explicit_networks_do_not_validate_an_unused_default(self):
        project = {"networks": {"back": {}, "default": {"ipam": {"driver": "x"}}}}
        for form, networks in (("list", ["back"]), ("mapping", {"back": {}})):
            with self.subTest(form=form):
                self.assertTrue(run({"w": {"image": "x", "networks": networks}}, project).ok)

    def test_service_without_networks_still_validates_default(self):
        project = {"networks": {"default": {"ipam": {"driver": "x"}}}}
        report = run({"w": {"image": "x"}}, project)
        self.assertEqual(keys(report.refusals), [("w", "networks.ipam")])
        self.assertIn("default", report.refusals[0].message)

    def test_empty_and_missing_networks_share_one_refusal_owned_by_the_first_service(self):
        project = {"networks": {"default": {"ipam": {"driver": "x"}}}}
        for form, networks in EMPTY_FORMS:
            with self.subTest(form=form):
                report = run(
                    {"w": {"image": "x", "networks": networks}, "v": {"image": "x"}},
                    project,
                )
                self.assertEqual(keys(report.refusals), [("w", "networks.ipam")])


class EmptyNetworksUpRefusalTests(ComposeFakeBase):
    def assert_refused_before_provisioning(self, result) -> None:
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("REFUSED:", result.stderr)
        self.assertIn("networks.ipam", result.stderr)
        self.assert_nothing_was_created()
        state = self.load_state()
        self.assertEqual(state["containers"], {})
        self.assertEqual(state["networks"], {})
        self.assertEqual(state["volumes"], {})
        self.assertEqual(state["action_log"], [])

    def test_up_refuses_ipam_on_default_for_empty_networks(self) -> None:
        for form, networks in EMPTY_YAML_FORMS:
            with self.subTest(form=form):
                self._seed_state()
                (self.root / "calls.log").unlink(missing_ok=True)
                self.write_compose(
                    """
                    services:
                      web:
                        image: nginx:latest
                        networks: %s
                        volumes: ["data:/d"]
                    networks:
                      default:
                        ipam:
                          driver: x
                    volumes:
                      data: {}
                    """ % networks
                )
                result = self.docker("compose", "up", "-d")
                self.assert_refused_before_provisioning(result)

    def test_up_selected_service_refuses_invalid_default_of_its_empty_networks_dependency(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                networks: [back]
                depends_on: [worker]
              worker:
                image: alpine:3.20
                networks: []
            networks:
              back: {}
              default:
                ipam:
                  driver: x
            """
        )
        result = self.docker("compose", "up", "-d", "web")
        self.assert_refused_before_provisioning(result)

    def test_up_no_deps_refuses_invalid_default_of_the_selected_empty_networks_service(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                networks: [back]
              worker:
                image: alpine:3.20
                networks: []
                depends_on: [web]
            networks:
              back: {}
              default:
                ipam:
                  driver: x
            """
        )
        result = self.docker("compose", "up", "-d", "--no-deps", "worker")
        self.assert_refused_before_provisioning(result)

    def test_waiver_for_another_key_does_not_lift_the_default_refusal(self) -> None:
        self._seed_state()
        self.env[security.ENV_ALLOW] = "sysctls"
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                networks: []
            networks:
              default:
                ipam:
                  driver: x
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assert_refused_before_provisioning(result)


class EmptyNetworksProfileTests(ComposeFakeBase):
    """Profiles only reach the shim through the official normalizer.

    The built-in parser refuses ``profiles`` outright, so these tests install
    the fake ``docker-compose`` and feed it the models a real
    ``docker-compose config --format json`` prints for this project: an
    inactive profile drops ``worker`` (and the unused ``default`` network), an
    active one keeps it with ``networks: []`` rewritten to ``{default: null}``.
    """

    DEFAULT = {"name": "demo_default", "ipam": {"driver": "x"}}
    BACK = {"name": "demo_back", "ipam": {}}
    WEB = {"image": "nginx:latest", "networks": {"back": None}}
    WORKER = {"image": "alpine:3.20", "networks": {"default": None}, "profiles": ["debug"]}

    def setUp(self) -> None:
        super().setUp()
        self.write_compose("services:\n  ignored:\n    image: busybox\n")

    def install_model(self, model: dict) -> None:
        executable = self.root / "docker-compose"
        executable.write_text(test_compose.ComposeE2ETests.FAKE_COMPOSE)
        executable.chmod(0o755)
        model_file = self.root / "model.json"
        model_file.write_text(json.dumps(model))
        self.env["FAKE_COMPOSE_MODEL"] = str(model_file)
        self.env["FAKE_COMPOSE_LOG"] = str(self.root / "compose-argv.log")
        self.env["CONTAINER_DOCKER_SHIM_COMPOSE"] = str(executable)

    def test_inactive_profile_service_does_not_validate_default(self) -> None:
        self.install_model(
            {"name": "demo", "services": {"web": self.WEB}, "networks": {"back": self.BACK}}
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("REFUSED:", result.stderr)
        self.assertEqual(self.created_networks(), ["demo_back"])
        self.assertNotIn("demo-worker-1", self.load_state()["containers"])

    def test_active_profile_service_refuses_invalid_default(self) -> None:
        model = {
            "name": "demo",
            "services": {"web": self.WEB, "worker": self.WORKER},
            "networks": {"back": self.BACK, "default": self.DEFAULT},
        }
        for how in ("flag", "environment"):
            with self.subTest(how=how):
                self._seed_state()
                (self.root / "calls.log").unlink(missing_ok=True)
                self.install_model(model)
                self.env.pop("COMPOSE_PROFILES", None)
                if how == "flag":
                    result = self.docker("compose", "--profile", "debug", "up", "-d")
                else:
                    self.env["COMPOSE_PROFILES"] = "debug"
                    result = self.docker("compose", "up", "-d")
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn("REFUSED:", result.stderr)
                self.assertIn("networks.ipam", result.stderr)
                self.assert_nothing_was_created()
                self.assertEqual(self.load_state()["containers"], {})


class EmptyNetworksSelectionTests(ComposeFakeBase):
    COMPOSE = """
        services:
          web:
            image: nginx:latest
            networks: [back]
            %s
          worker:
            image: alpine:3.20
            networks: []
        networks:
          back: {}
          default:
            ipam:
              driver: x
        """

    def assert_only_back_was_used(self, result) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("REFUSED:", result.stderr)
        self.assertEqual(self.created_networks(), ["demo_back"])
        state = self.load_state()
        self.assertNotIn("demo_default", state["networks"])
        self.assertNotIn("demo-worker-1", state["containers"])

    def test_up_selected_service_ignores_invalid_default_of_unselected_service(self) -> None:
        self.write_compose(self.COMPOSE % "")
        result = self.docker("compose", "up", "-d", "web")
        self.assert_only_back_was_used(result)

    def test_up_no_deps_ignores_invalid_default_of_skipped_dependency(self) -> None:
        self.write_compose(self.COMPOSE % "depends_on: [worker]")
        result = self.docker("compose", "up", "-d", "--no-deps", "web")
        self.assert_only_back_was_used(result)


if __name__ == "__main__":
    unittest.main()
