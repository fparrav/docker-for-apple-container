from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim import compose_normalizer  # noqa: E402


class BuildArgvTests(unittest.TestCase):
    def test_no_inputs_keeps_just_executable_and_tail(self):
        argv = compose_normalizer.build_argv(
            "docker-compose",
            compose_normalizer.Inputs(),
            ["config", "--format", "json"],
        )
        self.assertEqual(argv, ["docker-compose", "config", "--format", "json"])

    def test_files_alone(self):
        inputs = compose_normalizer.Inputs(files=("base.yml",))
        argv = compose_normalizer.build_argv("docker-compose", inputs, ["up"])
        self.assertEqual(argv, ["docker-compose", "-f", "base.yml", "up"])

    def test_env_files_alone(self):
        inputs = compose_normalizer.Inputs(env_files=(".env",))
        argv = compose_normalizer.build_argv("docker-compose", inputs, ["up"])
        self.assertEqual(argv, ["docker-compose", "--env-file", ".env", "up"])

    def test_profiles_alone(self):
        inputs = compose_normalizer.Inputs(profiles=("debug",))
        argv = compose_normalizer.build_argv("docker-compose", inputs, ["up"])
        self.assertEqual(argv, ["docker-compose", "--profile", "debug", "up"])

    def test_project_name_alone(self):
        inputs = compose_normalizer.Inputs(project_name="proj")
        argv = compose_normalizer.build_argv("docker-compose", inputs, ["up"])
        self.assertEqual(argv, ["docker-compose", "-p", "proj", "up"])

    def test_project_dir_alone(self):
        inputs = compose_normalizer.Inputs(project_dir="/project")
        argv = compose_normalizer.build_argv("docker-compose", inputs, ["up"])
        self.assertEqual(
            argv, ["docker-compose", "--project-directory", "/project", "up"]
        )

    def test_all_flags_in_the_fixed_order(self):
        inputs = compose_normalizer.Inputs(
            files=("base.yml", "override.yml"),
            env_files=(".env", "extra.env"),
            profiles=("debug", "metrics"),
            project_name="proj",
            project_dir="/project",
        )
        argv = compose_normalizer.build_argv(
            "docker-compose", inputs, ["config", "--format", "json"]
        )
        self.assertEqual(
            argv,
            [
                "docker-compose",
                "-f", "base.yml",
                "-f", "override.yml",
                "--env-file", ".env",
                "--env-file", "extra.env",
                "--profile", "debug",
                "--profile", "metrics",
                "-p", "proj",
                "--project-directory", "/project",
                "config",
                "--format",
                "json",
            ],
        )

    def test_repeated_flags_keep_their_order(self):
        inputs = compose_normalizer.Inputs(
            files=("z.yml", "a.yml", "m.yml"),
            profiles=("web", "db"),
        )
        argv = compose_normalizer.build_argv("docker-compose", inputs, [])
        self.assertEqual(
            argv,
            [
                "docker-compose",
                "-f", "z.yml",
                "-f", "a.yml",
                "-f", "m.yml",
                "--profile", "web",
                "--profile", "db",
            ],
        )

    def test_tail_is_appended_last(self):
        inputs = compose_normalizer.Inputs(
            files=("base.yml",),
            env_files=(".env",),
            profiles=("debug",),
            project_name="proj",
            project_dir="/project",
        )
        argv = compose_normalizer.build_argv(
            "docker-compose", inputs, ["config", "-q"]
        )
        self.assertEqual(
            argv,
            [
                "docker-compose",
                "-f", "base.yml",
                "--env-file", ".env",
                "--profile", "debug",
                "-p", "proj",
                "--project-directory", "/project",
                "config",
                "-q",
            ],
        )

    def test_empty_entries_are_omitted(self):
        inputs = compose_normalizer.Inputs(
            files=("", "base.yml", ""),
            env_files=("",),
            profiles=("",),
            project_name="",
            project_dir=None,
        )
        argv = compose_normalizer.build_argv("docker-compose", inputs, ["up"])
        self.assertEqual(argv, ["docker-compose", "-f", "base.yml", "up"])


class ResolveExecutableTests(unittest.TestCase):
    def test_explicit_value_is_used_without_calling_which(self):
        calls = []

        def which(name):
            calls.append(name)
            return "/usr/local/bin/docker-compose"

        env = {compose_normalizer.EXECUTABLE_ENV: "/opt/custom/docker-compose"}
        self.assertEqual(
            compose_normalizer.resolve_executable(
                env, which, (), is_file=lambda path: True
            ),
            "/opt/custom/docker-compose",
        )
        self.assertEqual(calls, [])

    def test_path_only_falls_back_to_which(self):
        calls = []

        def which(name):
            calls.append(name)
            return "/usr/local/bin/docker-compose"

        self.assertEqual(
            compose_normalizer.resolve_executable({}, which, ()),
            "/usr/local/bin/docker-compose",
        )
        self.assertEqual(calls, ["docker-compose"])

    def test_nothing_found_returns_none(self):
        self.assertIsNone(
            compose_normalizer.resolve_executable(
                {}, lambda name: None, ()
            )
        )

    def test_explicit_but_missing_raises(self):
        def which(name):
            self.fail("which must not be called for an explicit value")

        with self.assertRaises(compose_normalizer.NormalizerError) as ctx:
            compose_normalizer.resolve_executable(
                {compose_normalizer.EXECUTABLE_ENV: "/missing/docker-compose"},
                which,
                (),
                is_file=lambda path: False,
            )
        message = str(ctx.exception)
        self.assertIn(compose_normalizer.EXECUTABLE_ENV, message)
        self.assertIn("/missing/docker-compose", message)

    def test_empty_executable_env_is_treated_as_unset(self):
        calls = []

        def which(name):
            calls.append(name)
            return "/usr/local/bin/docker-compose"

        env = {compose_normalizer.EXECUTABLE_ENV: ""}
        self.assertEqual(
            compose_normalizer.resolve_executable(env, which, ()),
            "/usr/local/bin/docker-compose",
        )
        self.assertEqual(calls, ["docker-compose"])


class LoopGuardTests(unittest.TestCase):
    LAUNCHER = "/opt/real/docker-compose"

    def _realpath(self, mapping=None):
        table = dict(mapping or {})

        def realpath(path):
            return table.get(path, path)

        return realpath

    def test_explicit_executable_resolving_to_a_launcher_raises_loop(self):
        calls = []

        def which(name):
            calls.append(name)
            return None

        env = {compose_normalizer.EXECUTABLE_ENV: "/opt/link/docker-compose"}
        with self.assertRaises(compose_normalizer.NormalizerError) as ctx:
            compose_normalizer.resolve_executable(
                env,
                which,
                (self.LAUNCHER,),
                is_file=lambda path: True,
                realpath=self._realpath(
                    {"/opt/link/docker-compose": self.LAUNCHER}
                ),
            )
        message = str(ctx.exception)
        self.assertIn("loop", message)
        self.assertIn("/opt/link/docker-compose", message)
        self.assertEqual(calls, [])

    def test_path_candidate_that_is_the_launcher_raises_loop(self):
        with self.assertRaises(compose_normalizer.NormalizerError) as ctx:
            compose_normalizer.resolve_executable(
                {},
                lambda name: self.LAUNCHER,
                (self.LAUNCHER,),
                is_file=lambda path: True,
                realpath=self._realpath(),
            )
        message = str(ctx.exception)
        self.assertIn("loop", message)
        self.assertIn(self.LAUNCHER, message)

    def test_marker_already_in_env_raises_reentered(self):
        with self.assertRaises(compose_normalizer.NormalizerError) as ctx:
            compose_normalizer.resolve_executable(
                {compose_normalizer.MARKER_ENV: "1"},
                lambda name: "/usr/local/bin/docker-compose",
                (),
            )
        self.assertIn("re-entered", str(ctx.exception))

    def test_marker_with_no_candidate_does_not_raise(self):
        env = {compose_normalizer.MARKER_ENV: "1"}
        self.assertIsNone(
            compose_normalizer.resolve_executable(env, lambda name: None, ())
        )

    def test_child_env_sets_marker_without_mutating(self):
        env = {"PATH": "/usr/bin", "FOO": "bar"}
        result = compose_normalizer.child_env(env)
        self.assertIsNot(result, env)
        self.assertEqual(result[compose_normalizer.MARKER_ENV], "1")
        self.assertEqual(result["PATH"], "/usr/bin")
        self.assertEqual(result["FOO"], "bar")
        self.assertNotIn(compose_normalizer.MARKER_ENV, env)
        self.assertEqual(env, {"PATH": "/usr/bin", "FOO": "bar"})


class LoadNormalizedTests(unittest.TestCase):
    def _recording_run(self, result=None, error=None):
        calls = []

        def run(argv, env):
            calls.append((argv, env))
            if error is not None:
                raise error
            return result

        return run, calls

    def _inputs(self):
        return compose_normalizer.Inputs(
            files=("base.yml",), project_name="proj"
        )

    def test_success_passes_argv_and_marker_env_and_returns_model_and_stderr(self):
        payload = {"services": {"web": {"image": "nginx"}}, "volumes": {}}
        result = types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="a warning line\n",
        )
        run, calls = self._recording_run(result=result)
        model, stderr = compose_normalizer.load_normalized(
            "/usr/local/bin/docker-compose", self._inputs(), run, {"PATH": "/usr/bin"}
        )
        self.assertEqual(model, payload)
        self.assertEqual(stderr, "a warning line\n")
        self.assertEqual(len(calls), 1)
        argv, env = calls[0]
        self.assertEqual(
            argv,
            [
                "/usr/local/bin/docker-compose",
                "-f",
                "base.yml",
                "-p",
                "proj",
                "config",
                "--format",
                "json",
            ],
        )
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertEqual(env[compose_normalizer.MARKER_ENV], "1")

    def test_nonzero_exit_raises_with_stripped_stderr_and_code_one(self):
        result = types.SimpleNamespace(returncode=1, stdout="", stderr="  bad file\n")
        run, calls = self._recording_run(result=result)
        with self.assertRaises(compose_normalizer.NormalizerError) as ctx:
            compose_normalizer.load_normalized(
                "docker-compose", self._inputs(), run, {}
            )
        self.assertEqual(str(ctx.exception), "bad file")
        self.assertEqual(ctx.exception.code, 1)

    def test_nonzero_exit_with_blank_stderr_uses_default_message(self):
        result = types.SimpleNamespace(returncode=3, stdout="", stderr="   \n")
        run, calls = self._recording_run(result=result)
        with self.assertRaises(compose_normalizer.NormalizerError) as ctx:
            compose_normalizer.load_normalized(
                "docker-compose", self._inputs(), run, {}
            )
        self.assertEqual(str(ctx.exception), "docker-compose config failed")
        self.assertEqual(ctx.exception.code, 1)

    def test_invalid_json_raises_naming_the_problem(self):
        result = types.SimpleNamespace(returncode=0, stdout="not json", stderr="")
        run, calls = self._recording_run(result=result)
        with self.assertRaises(compose_normalizer.NormalizerError) as ctx:
            compose_normalizer.load_normalized(
                "docker-compose", self._inputs(), run, {}
            )
        self.assertIn("invalid JSON", str(ctx.exception))

    def test_json_list_raises(self):
        result = types.SimpleNamespace(returncode=0, stdout="[]", stderr="")
        run, calls = self._recording_run(result=result)
        with self.assertRaises(compose_normalizer.NormalizerError) as ctx:
            compose_normalizer.load_normalized(
                "docker-compose", self._inputs(), run, {}
            )
        self.assertIn("list", str(ctx.exception))

    def test_oserror_from_run_raises_its_text(self):
        run, calls = self._recording_run(error=OSError("spawn failed"))
        with self.assertRaises(compose_normalizer.NormalizerError) as ctx:
            compose_normalizer.load_normalized(
                "docker-compose", self._inputs(), run, {}
            )
        self.assertEqual(str(ctx.exception), "spawn failed")


class ExecutableVersionTests(unittest.TestCase):
    def _recording_run(self, result=None, error=None):
        calls = []

        def run(argv, env):
            calls.append((argv, env))
            if error is not None:
                raise error
            return result

        return run, calls

    def test_success_returns_stripped_stdout(self):
        result = types.SimpleNamespace(
            returncode=0, stdout="v1.29.0, build abc123\n", stderr=""
        )
        run, calls = self._recording_run(result=result)
        version = compose_normalizer.executable_version(
            "/usr/local/bin/docker-compose", run, {"PATH": "/usr/bin"}
        )
        self.assertEqual(version, "v1.29.0, build abc123")
        self.assertEqual(len(calls), 1)
        argv, env = calls[0]
        self.assertEqual(argv, ["/usr/local/bin/docker-compose", "version", "--short"])
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertEqual(env[compose_normalizer.MARKER_ENV], "1")

    def test_trailing_newline_is_stripped(self):
        result = types.SimpleNamespace(returncode=0, stdout="v2.0.0\n", stderr="")
        run, calls = self._recording_run(result=result)
        self.assertEqual(
            compose_normalizer.executable_version("docker-compose", run, {}),
            "v2.0.0",
        )

    def test_nonzero_exit_returns_unknown(self):
        result = types.SimpleNamespace(
            returncode=1, stdout="v2.0.0\n", stderr="boom"
        )
        run, calls = self._recording_run(result=result)
        self.assertEqual(
            compose_normalizer.executable_version("docker-compose", run, {}),
            "unknown",
        )

    def test_empty_output_returns_unknown(self):
        result = types.SimpleNamespace(returncode=0, stdout="   \n", stderr="")
        run, calls = self._recording_run(result=result)
        self.assertEqual(
            compose_normalizer.executable_version("docker-compose", run, {}),
            "unknown",
        )

    def test_oserror_returns_unknown(self):
        run, calls = self._recording_run(error=OSError("spawn failed"))
        self.assertEqual(
            compose_normalizer.executable_version("docker-compose", run, {}),
            "unknown",
        )
        self.assertEqual(len(calls), 1)


class ConvertModelTests(unittest.TestCase):
    """convert_model against the verbatim docker-compose 5.6.0 output."""

    VERSION = "5.6.0"

    @staticmethod
    def _load_fixture(filename: str) -> dict:
        path = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "compose"
            / "normalized"
            / filename
        )
        text = path.read_text(encoding="utf-8").replace("__PROJECT_DIR__", "/project")
        return json.loads(text)

    def _convert(self, filename: str) -> dict:
        return compose_normalizer.convert_model(
            self._load_fixture(filename), self.VERSION
        )

    def test_full_default_model(self):
        model = self._convert("full-default.json")
        self.assertEqual(set(model), {"name", "services", "networks", "volumes"})
        self.assertEqual(model["name"], "full")
        self.assertEqual(
            model["networks"], {"default": {"name": "full_default", "ipam": {}}}
        )
        self.assertEqual(model["volumes"], {"data": {"name": "full_data"}})
        self.assertNotIn("x-common", model)
        self.assertEqual(set(model["services"]), {"api", "db"})

    def test_api_ports_rendered_as_strings(self):
        api = self._convert("full-default.json")["services"]["api"]
        self.assertEqual(
            api["ports"], ["8080:80", "127.0.0.1:9090:90/udp", "8081:81"]
        )

    def test_db_none_keys_dropped_without_recursion(self):
        db = self._convert("full-default.json")["services"]["db"]
        self.assertNotIn("entrypoint", db)
        self.assertNotIn("command", db)
        # A mapping whose values are null is kept: only top-level None keys go.
        self.assertEqual(db["networks"], {"default": None})

    def test_api_entrypoint_dropped_command_kept(self):
        api = self._convert("full-default.json")["services"]["api"]
        self.assertNotIn("entrypoint", api)
        self.assertEqual(api["command"], ["sh", "-c", "echo starting\nsleep infinity"])

    def test_api_values_copied_unchanged(self):
        api = self._convert("full-default.json")["services"]["api"]
        self.assertEqual(
            api["environment"], {"EXTRA": "from-override", "LOG_LEVEL": "debug"}
        )
        self.assertEqual(
            api["volumes"],
            [
                {"type": "volume", "source": "data", "target": "/data", "volume": {}},
                {
                    "type": "bind",
                    "source": "/project/conf",
                    "target": "/etc/conf",
                    "read_only": True,
                    "bind": {},
                },
            ],
        )
        self.assertEqual(
            api["build"],
            {"context": "/project/api", "dockerfile": "Dockerfile", "args": {"FOO": "bar"}},
        )
        self.assertEqual(
            api["depends_on"],
            {"db": {"condition": "service_healthy", "required": True}},
        )
        self.assertEqual(
            api["healthcheck"],
            {"test": ["CMD-SHELL", "true"], "timeout": "5s", "interval": "1m30s"},
        )

    def test_debug_service_only_in_profile_fixture(self):
        default = self._convert("full-default.json")
        profiled = self._convert("full-profile-debug.json")
        self.assertNotIn("debug", default["services"])
        self.assertIn("debug", profiled["services"])
        debug = profiled["services"]["debug"]
        self.assertEqual(debug["command"], ["sleep", "infinity"])
        self.assertEqual(debug["profiles"], ["debug"])
        self.assertNotIn("entrypoint", debug)

    def test_port_without_published_is_bare_target(self):
        raw = {
            "name": "p",
            "services": {
                "s": {"image": "redis:7", "ports": [{"target": 6379, "protocol": "tcp"}]}
            },
        }
        model = compose_normalizer.convert_model(raw, self.VERSION)
        self.assertEqual(model["services"]["s"]["ports"], ["6379"])

    def test_udp_port_without_host_side(self):
        raw = {
            "name": "p",
            "services": {"s": {"image": "x", "ports": [{"target": 90, "protocol": "udp"}]}}
        }
        model = compose_normalizer.convert_model(raw, self.VERSION)
        self.assertEqual(model["services"]["s"]["ports"], ["90/udp"])

    def test_missing_networks_and_volumes_become_empty(self):
        model = compose_normalizer.convert_model(
            {"name": "p", "services": {"s": {"image": "x"}}}, self.VERSION
        )
        self.assertEqual(
            model,
            {"name": "p", "services": {"s": {"image": "x"}}, "networks": {}, "volumes": {}},
        )

    def test_non_mapping_raw_raises_naming_the_version(self):
        with self.assertRaises(compose_normalizer.NormalizerError) as ctx:
            compose_normalizer.convert_model([], self.VERSION)
        message = str(ctx.exception)
        self.assertIn("Docker Compose %s" % self.VERSION, message)
        self.assertIn("mapping", message)

    def test_missing_name_raises_naming_the_version_and_key(self):
        with self.assertRaises(compose_normalizer.NormalizerError) as ctx:
            compose_normalizer.convert_model({"services": {}}, self.VERSION)
        message = str(ctx.exception)
        self.assertIn("Docker Compose %s" % self.VERSION, message)
        self.assertIn("name", message)

    def test_non_string_name_raises_naming_the_version_and_key(self):
        with self.assertRaises(compose_normalizer.NormalizerError) as ctx:
            compose_normalizer.convert_model({"name": 42, "services": {}}, self.VERSION)
        message = str(ctx.exception)
        self.assertIn("Docker Compose %s" % self.VERSION, message)
        self.assertIn("name", message)

    def test_non_mapping_services_raises_naming_the_version_and_key(self):
        with self.assertRaises(compose_normalizer.NormalizerError) as ctx:
            compose_normalizer.convert_model(
                {"name": "p", "services": ["s"]}, self.VERSION
            )
        message = str(ctx.exception)
        self.assertIn("Docker Compose %s" % self.VERSION, message)
        self.assertIn("services", message)

    def test_non_mapping_service_raises_naming_the_version_and_service(self):
        with self.assertRaises(compose_normalizer.NormalizerError) as ctx:
            compose_normalizer.convert_model(
                {"name": "p", "services": {"web": ["nope"]}}, self.VERSION
            )
        message = str(ctx.exception)
        self.assertIn("Docker Compose %s" % self.VERSION, message)
        self.assertIn("web", message)


if __name__ == "__main__":
    unittest.main()
