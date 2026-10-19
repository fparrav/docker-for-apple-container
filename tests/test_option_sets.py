from __future__ import annotations

import ast
import inspect
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim import cli  # noqa: E402
from container_docker_shim import compose  # noqa: E402

TIER = "unit"


# Dashed literals in _parse_run_options and cmd_build that are not option
# flags: "-" is the startswith() prefix test and "--" is the end-of-options
# marker.
_SENTINEL_LITERALS = frozenset({"-", "--"})


class RunOptionSetTests(unittest.TestCase):
    def test_option_sets_are_pairwise_disjoint(self):
        sets = {
            "RUN_VALUE_OPTIONS": cli.RUN_VALUE_OPTIONS,
            "RUN_BOOL_OPTIONS": cli.RUN_BOOL_OPTIONS,
            "RUN_REFUSED_OPTIONS": cli.RUN_REFUSED_OPTIONS,
            "RUN_EXPLICIT_OPTIONS": cli.RUN_EXPLICIT_OPTIONS,
        }
        names = list(sets)
        for index, first_name in enumerate(names):
            for second_name in names[index + 1 :]:
                overlap = sets[first_name] & sets[second_name]
                self.assertEqual(
                    overlap,
                    frozenset(),
                    f"{first_name} and {second_name} overlap: {sorted(overlap)}",
                )

    def test_every_option_literal_in_parse_run_options_belongs_to_a_set(self):
        source = inspect.getsource(cli._parse_run_options)
        literals = {
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("-")
            and node.value not in _SENTINEL_LITERALS
        }
        union = (
            cli.RUN_VALUE_OPTIONS
            | cli.RUN_BOOL_OPTIONS
            | cli.RUN_REFUSED_OPTIONS
            | cli.RUN_EXPLICIT_OPTIONS
        )
        missing = sorted(literals - union)
        self.assertEqual(
            missing,
            [],
            "literals in _parse_run_options missing from the option sets: "
            + ", ".join(missing),
        )


class BuildOptionSetTests(unittest.TestCase):
    def test_option_sets_are_pairwise_disjoint(self):
        sets = {
            "BUILD_VALUE_OPTIONS": cli.BUILD_VALUE_OPTIONS,
            "BUILD_BOOL_OPTIONS": cli.BUILD_BOOL_OPTIONS,
            "BUILD_NOOP_OPTIONS": cli.BUILD_NOOP_OPTIONS,
            "BUILD_REFUSED_OPTIONS": cli.BUILD_REFUSED_OPTIONS,
            "BUILD_EXPLICIT_OPTIONS": cli.BUILD_EXPLICIT_OPTIONS,
        }
        names = list(sets)
        for index, first_name in enumerate(names):
            for second_name in names[index + 1 :]:
                overlap = sets[first_name] & sets[second_name]
                self.assertEqual(
                    overlap,
                    frozenset(),
                    f"{first_name} and {second_name} overlap: {sorted(overlap)}",
                )

    def test_every_option_literal_in_cmd_build_belongs_to_a_set(self):
        source = inspect.getsource(cli.cmd_build)
        literals = {
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("-")
            and node.value not in _SENTINEL_LITERALS
        }
        union = (
            cli.BUILD_VALUE_OPTIONS
            | cli.BUILD_BOOL_OPTIONS
            | cli.BUILD_NOOP_OPTIONS
            | cli.BUILD_REFUSED_OPTIONS
            | cli.BUILD_EXPLICIT_OPTIONS
        )
        missing = sorted(literals - union)
        self.assertEqual(
            missing,
            [],
            "literals in cmd_build missing from the option sets: "
            + ", ".join(missing),
        )


class ComposeKeySetTests(unittest.TestCase):
    # A plausible YAML value per supported key. Keys whose meaning requires
    # structure (build needs a context, healthcheck needs a test) are given in
    # their valid form so the whole document stays loadable.
    KEY_VALUES = {
        "build": ".",
        "cap_add": '["NET_ADMIN"]',
        "cap_drop": '["NET_RAW"]',
        "command": '["sh", "-c", "echo ok"]',
        "container_name": "web",
        "cpus": "2",
        "depends_on": "[]",
        "deploy": "{resources: {limits: {cpus: 2, memory: 512mb}}}",
        "dns": '["1.1.1.1"]',
        "dns_opt": '["ndots:2"]',
        "dns_search": '["example.com"]',
        "entrypoint": '["sleep", "3600"]',
        "env_file": '["config.env"]',
        "environment": "{A: 1}",
        "healthcheck": '{test: ["CMD", "true"], interval: "1s", retries: 3}',
        "image": "img:1",
        "init": "true",
        "labels": "{team: platform}",
        "mem_limit": "512mb",
        "networks": "[]",
        "platform": "linux/arm64",
        "ports": '["8080:80"]',
        "read_only": "true",
        "shm_size": "128mb",
        "stdin_open": "true",
        "tmpfs": '["/tmp:ro"]',
        "tty": "true",
        "ulimits": "{nofile: {soft: 20000, hard: 40000}}",
        "user": "nobody",
        "volumes": "[]",
        "working_dir": "/app",
        "x-shim-boot-hosts": "false",
    }

    def test_supported_and_unsupported_keys_are_disjoint(self):
        overlap = compose.SUPPORTED_SERVICE_KEYS & set(compose.UNSUPPORTED_SERVICE_KEYS)
        self.assertEqual(
            overlap,
            frozenset(),
            f"keys listed as both supported and unsupported: {sorted(overlap)}",
        )

    def test_security_keys_are_disjoint_from_supported(self):
        overlap = compose.SUPPORTED_SERVICE_KEYS & set(compose.SECURITY_OPTION_KEYS)
        self.assertEqual(
            overlap,
            frozenset(),
            f"security keys also listed as supported: {sorted(overlap)}",
        )

    def test_every_supported_key_is_accepted_by_the_loader(self):
        self.assertEqual(
            set(self.KEY_VALUES),
            compose.SUPPORTED_SERVICE_KEYS,
            "the per-key value table must cover every supported key",
        )
        for key in sorted(compose.SUPPORTED_SERVICE_KEYS):
            with self.subTest(key=key):
                lines = ["services:", "  web:", "    image: img:1"]
                if key != "image":
                    lines.append(f"    {key}: {self.KEY_VALUES[key]}")
                doc = compose.parse_yaml("\n".join(lines) + "\n")
                project = compose.Project("p", doc, "/tmp/p")
                self.assertIn("web", project.services)


if __name__ == "__main__":
    unittest.main()
