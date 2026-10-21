"""Live regression test against the real ``docker-compose`` binary.

Copies ``tests/fixtures/compose/full/`` into a throwaway directory (the
``dot-env`` file is renamed to ``.env``), runs the installed binary with
``-p full`` on it, and compares ``config --format json`` against the
recorded normalized fixture, so a drifting Compose release is detected.
The result must not depend on the temporary directory name: the fixture
carries a ``__PROJECT_DIR__`` placeholder that is substituted with the
realpath of the project directory before comparing.

The whole class is skipped unless ``docker-compose`` is on ``PATH`` and
reports exactly the supported version. No daemon is needed: the child
gets ``DOCKER_HOST=unix:///nonexistent.sock``.

:class:`EnvFileOfficialTests` covers the env-file inputs the same way: it
runs ``bin/docker compose ... config`` (the real ``docker-compose`` as the
normalizer) and the official binary directly on the same inputs and
compares the normalized model the shim loads with the model
``docker-compose config --format json`` produces.

:class:`BuildSecretsOfficialTests` covers the build-secrets project the same
way, copying the fixture into a fresh temporary directory per test: the shim
and the official binary must load the same model, ``compose_build_secrets``
resolves the file and environment secrets from it, an undefined secret is
rejected by the official binary itself, and the runtime secret the service
mounts is still refused by the gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable, Mapping, Optional


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim import (  # noqa: E402
    compose_build_secrets,
    compose_capabilities,
    compose_normalizer,
    security,
)
from container_docker_shim.compose_build_secrets import BuildSecret  # noqa: E402

TIER = "fake"


SUPPORTED_VERSION = "5.6.0"
PROJECT_NAME = "full"
FULL_FIXTURE = ROOT / "tests" / "fixtures" / "compose" / "full"
NORMALIZED_FIXTURE = ROOT / "tests" / "fixtures" / "compose" / "normalized" / "full-default.json"
ENV_FILES_FIXTURE = ROOT / "tests" / "fixtures" / "compose" / "env-files"
BUILD_SECRETS_FIXTURE = ROOT / "tests" / "fixtures" / "compose" / "build-secrets"
DOCKER = ROOT / "bin" / "docker"

BUILD_SECRETS_PROJECT = "bsec"


class OfficialComposeTests(unittest.TestCase):
    executable: Optional[str] = None
    project_dir: Optional[str] = None
    env: dict = {}
    _temp_root: Optional[Path] = None

    @classmethod
    def setUpClass(cls) -> None:
        executable = shutil.which("docker-compose")
        if executable is None:
            raise unittest.SkipTest("docker-compose is not on PATH")
        version = compose_normalizer.executable_version(
            executable, cls._run(cwd=None), cls._child_env()
        )
        if version != SUPPORTED_VERSION:
            raise unittest.SkipTest(
                "docker-compose %s is installed; the official binary "
                "must be exactly %s" % (version, SUPPORTED_VERSION)
            )
        cls.executable = executable
        cls.env = cls._child_env()
        cls._temp_root = Path(tempfile.mkdtemp(prefix="compose-official-"))
        project_dir = cls._temp_root / "project"
        shutil.copytree(FULL_FIXTURE, project_dir)
        shutil.move(str(project_dir / "dot-env"), str(project_dir / ".env"))
        # The realpath, so the result does not depend on the temp name
        # (e.g. /var vs /private/var) and matches the fixture substitution.
        cls.project_dir = str(project_dir.resolve())

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._temp_root is not None:
            shutil.rmtree(cls._temp_root, ignore_errors=True)

    @staticmethod
    def _child_env() -> dict:
        """Environment for the compose child: daemon off, no leaked vars."""
        env = dict(os.environ)
        env["DOCKER_HOST"] = "unix:///nonexistent.sock"
        # Any COMPOSE_* variable of the developer's shell could change the output.
        for name in [name for name in env if name.startswith("COMPOSE_")]:
            del env[name]
        return env

    @staticmethod
    def _run(cwd: Optional[str]) -> Callable[..., subprocess.CompletedProcess]:
        """A ``run`` for :func:`load_normalized` in ``cwd`` (a no-op daemon)."""

        def run(argv: list, env: Mapping) -> subprocess.CompletedProcess:
            return subprocess.run(
                argv,
                env=env,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=60,
            )

        return run

    def test_config_json_matches_recorded_fixture(self) -> None:
        """The live ``config --format json`` equals the recorded model."""
        inputs = compose_normalizer.Inputs(
            project_name=PROJECT_NAME,
            project_dir=self.project_dir,
        )
        model, _ = compose_normalizer.load_normalized(
            self.executable,
            inputs,
            self._run(self.project_dir),
            self.env,
        )
        expected_text = NORMALIZED_FIXTURE.read_text(encoding="utf-8")
        expected = json.loads(
            expected_text.replace("__PROJECT_DIR__", self.project_dir)
        )
        self.assertEqual(model, expected)

    def test_config_quiet_exits_zero(self) -> None:
        """The recorded project is valid: ``config -q`` exits 0."""
        result = subprocess.run(
            [self.executable, "-p", PROJECT_NAME, "config", "-q"],
            env=self.env,
            capture_output=True,
            text=True,
            cwd=self.project_dir,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)


class EnvFileOfficialTests(unittest.TestCase):
    executable: Optional[str] = None
    project_dir: Optional[str] = None
    env: dict = {}
    _temp_root: Optional[Path] = None

    @classmethod
    def setUpClass(cls) -> None:
        executable = shutil.which("docker-compose")
        if executable is None:
            raise unittest.SkipTest("docker-compose is not on PATH")
        version = compose_normalizer.executable_version(
            executable, cls._run(cwd=None), cls._child_env()
        )
        if version != SUPPORTED_VERSION:
            raise unittest.SkipTest(
                "docker-compose %s is installed; the official binary "
                "must be exactly %s" % (version, SUPPORTED_VERSION)
            )
        cls.executable = executable
        cls.env = cls._child_env()
        cls._temp_root = Path(tempfile.mkdtemp(prefix="compose-envfiles-"))
        project_dir = cls._temp_root / "project"
        shutil.copytree(ENV_FILES_FIXTURE, project_dir)
        shutil.move(str(project_dir / "dot-env"), str(project_dir / ".env"))
        cls.project_dir = str(project_dir.resolve())

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._temp_root is not None:
            shutil.rmtree(cls._temp_root, ignore_errors=True)

    @staticmethod
    def _child_env() -> dict:
        """Environment for the shim and the compose child: daemon off, no leaked vars."""
        env = dict(os.environ)
        env["DOCKER_HOST"] = "unix:///nonexistent.sock"
        # Any COMPOSE_* variable of the developer's shell could change the
        # output, and the shim variables would redirect the normalizer.
        for name in [
            name
            for name in env
            if name.startswith("COMPOSE_")
            or name.startswith("CONTAINER_DOCKER_SHIM_")
        ]:
            del env[name]
        return env

    @staticmethod
    def _run(cwd: Optional[str]) -> Callable[..., subprocess.CompletedProcess]:
        """A ``run`` for :func:`load_normalized` in ``cwd`` (a no-op daemon)."""

        def run(argv: list, env: Mapping) -> subprocess.CompletedProcess:
            return subprocess.run(
                argv,
                env=env,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=60,
            )

        return run

    def _shim(
        self, argv: list, env: dict, cwd: str
    ) -> subprocess.CompletedProcess:
        """Run the shim's ``compose`` entry point on ``argv``."""
        return subprocess.run(
            [sys.executable, str(DOCKER), "compose", *argv],
            env=env,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=60,
        )

    def _direct(
        self, argv: list, env: dict, cwd: str
    ) -> subprocess.CompletedProcess:
        """Run the official binary on ``argv``."""
        return subprocess.run(
            [self.executable, *argv],
            env=env,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=60,
        )

    def _assert_same_model(
        self, argv: list, *, env: Optional[dict] = None, cwd: Optional[str] = None
    ) -> dict:
        """The shim and the official binary must load the same model.

        ``argv`` is the global flags plus ``config``; the official run gets
        ``--format json`` appended. Returns the model the shim loaded.
        """
        env = dict(self.env if env is None else env)
        cwd = cwd or self.project_dir
        shim = self._shim(argv, env, cwd)
        direct = self._direct(argv + ["--format", "json"], env, cwd)
        self.assertEqual(shim.returncode, 0, msg=shim.stderr)
        self.assertEqual(direct.returncode, 0, msg=direct.stderr)
        model = json.loads(shim.stdout)
        self.assertEqual(model, json.loads(direct.stdout))
        return model

    def test_one_env_file(self) -> None:
        """One relative --env-file, without --project-directory."""
        model = self._assert_same_model(["--env-file", "e1.env", "config"])
        environment = model["services"]["app"]["environment"]
        self.assertEqual(
            {key: environment[key] for key in ("B", "C", "D")},
            {"B": "1", "C": "1", "D": ""},
        )

    def test_two_env_files(self) -> None:
        """Two --env-file flags: the later file wins, as in the official binary."""
        model = self._assert_same_model(
            ["--env-file", "e1.env", "--env-file", "e2.env", "config"]
        )
        environment = model["services"]["app"]["environment"]
        self.assertEqual(
            {key: environment[key] for key in ("B", "C", "D")},
            {"B": "1", "C": "2", "D": "2"},
        )

    def test_three_env_files(self) -> None:
        """Three --env-file flags, including the service's env_file as a global file."""
        model = self._assert_same_model(
            [
                "--env-file",
                "e1.env",
                "--env-file",
                "e2.env",
                "--env-file",
                "svc.env",
                "config",
            ]
        )
        environment = model["services"]["app"]["environment"]
        self.assertEqual(
            {key: environment[key] for key in ("B", "C", "D")},
            {"B": "1", "C": "2", "D": "2"},
        )
        # As a global --env-file, svc.env is available for interpolation.
        self.assertEqual(environment["SVC_INTERP"], "from_svc")

    def test_shell_variable_overrides_env_file(self) -> None:
        """A variable set in the shell wins over every env file."""
        env = dict(self.env)
        env["B"] = "from-shell"
        model = self._assert_same_model(["--env-file", "e1.env", "config"], env=env)
        self.assertEqual(
            model["services"]["app"]["environment"]["B"], "from-shell"
        )

    def test_missing_env_file(self) -> None:
        """A missing explicit file exits 1 naming it, as the official binary does."""
        env = dict(self.env)
        shim = self._shim(["--env-file", "nope.env", "config"], env, self.project_dir)
        direct = self._direct(
            ["--env-file", "nope.env", "config"], env, self.project_dir
        )
        self.assertEqual(direct.returncode, 1, msg=direct.stderr)
        self.assertEqual(shim.returncode, direct.returncode, msg=shim.stderr)
        self.assertIn("couldn't find env file", shim.stderr)
        self.assertIn(os.path.join(self.project_dir, "nope.env"), shim.stderr)

    def test_relative_env_file_with_project_directory(self) -> None:
        """A relative --env-file resolves against the cwd, with --project-directory too.

        The official binary resolves it against the current directory, not
        the project directory, so the file is copied into the cwd.
        """
        shutil.copy(ENV_FILES_FIXTURE / "e1.env", self._temp_root / "e1.env")
        self._assert_same_model(
            ["--project-directory", "project", "--env-file", "e1.env", "config"],
            cwd=str(self._temp_root),
        )

    def test_dot_env_used_without_env_file(self) -> None:
        """Without --env-file the project .env is read, as in the official binary."""
        model = self._assert_same_model(["config"])
        self.assertEqual(
            model["services"]["app"]["environment"]["A"], "from_dot_env"
        )

    def test_dot_env_ignored_with_env_file(self) -> None:
        """Once --env-file is given the project .env is not read."""
        model = self._assert_same_model(["--env-file", "e1.env", "config"])
        self.assertEqual(model["services"]["app"]["environment"]["A"], "")

    def test_service_env_file_not_used_for_interpolation(self) -> None:
        """The service env_file feeds the service environment, never interpolation."""
        model = self._assert_same_model(["config"])
        environment = model["services"]["app"]["environment"]
        self.assertEqual(environment["SVC"], "from_svc")
        self.assertEqual(environment["SVC_INTERP"], "")

    def test_compose_env_files_without_flag(self) -> None:
        """COMPOSE_ENV_FILES is honoured when no --env-file is given."""
        env = dict(self.env)
        env["COMPOSE_ENV_FILES"] = "e1.env,e2.env"
        model = self._assert_same_model(["config"], env=env)
        environment = model["services"]["app"]["environment"]
        self.assertEqual(
            {key: environment[key] for key in ("B", "C", "D")},
            {"B": "1", "C": "2", "D": "2"},
        )

    def test_flag_wins_over_compose_env_files(self) -> None:
        """A --env-file on the command line takes precedence over COMPOSE_ENV_FILES."""
        env = dict(self.env)
        env["COMPOSE_ENV_FILES"] = "e1.env,e2.env"
        model = self._assert_same_model(["--env-file", "e2.env", "config"], env=env)
        environment = model["services"]["app"]["environment"]
        self.assertEqual(
            {key: environment[key] for key in ("B", "C", "D")},
            {"B": "", "C": "2", "D": "2"},
        )

    def test_equals_form_matches_separate_form(self) -> None:
        """--env-file=X behaves exactly like --env-file X."""
        equals = self._assert_same_model(["--env-file=e1.env", "config"])
        separate = self._shim(
            ["--env-file", "e1.env", "config"], self.env, self.project_dir
        )
        self.assertEqual(separate.returncode, 0, msg=separate.stderr)
        self.assertEqual(equals, json.loads(separate.stdout))


class _Named:
    """Duck-typed service for :func:`compose_build_secrets.resolve` (needs a name)."""

    def __init__(self, name: str) -> None:
        self.name = name


class BuildSecretsOfficialTests(unittest.TestCase):
    """The build-secrets project through the real 5.6.0 normalizer.

    The fixture (``tests/fixtures/compose/build-secrets/``) is copied into a
    fresh temporary directory per test. Its one service builds from ``.``
    with a file secret (string entry) and a renamed environment secret, and
    mounts one runtime secret no build references; the top level defines all
    three. Skipped with a reason unless ``docker-compose`` is exactly 5.6.0.
    No daemon is needed: the tests run ``config`` only, plus the gate refusal
    of ``up`` that happens before any runtime call.
    """

    executable: Optional[str] = None
    project_dir: Optional[str] = None
    env: dict = {}
    _temp_root: Optional[Path] = None

    @classmethod
    def setUpClass(cls) -> None:
        executable = shutil.which("docker-compose")
        if executable is None:
            raise unittest.SkipTest("docker-compose is not on PATH")
        version = compose_normalizer.executable_version(
            executable, cls._run(cwd=None), cls._child_env()
        )
        if version != SUPPORTED_VERSION:
            raise unittest.SkipTest(
                "docker-compose %s is installed; the official binary "
                "must be exactly %s" % (version, SUPPORTED_VERSION)
            )
        cls.executable = executable
        cls.env = cls._child_env()

    def setUp(self) -> None:
        self._temp_root = Path(tempfile.mkdtemp(prefix="compose-buildsecrets-"))
        project_dir = self._temp_root / "project"
        shutil.copytree(BUILD_SECRETS_FIXTURE, project_dir)
        # The realpath, so the result does not depend on the temp name
        # (e.g. /var vs /private/var) and matches the fixture substitution.
        self.project_dir = str(project_dir.resolve())

    def tearDown(self) -> None:
        if self._temp_root is not None:
            shutil.rmtree(self._temp_root, ignore_errors=True)
            self._temp_root = None

    @staticmethod
    def _child_env() -> dict:
        """Environment for the shim and the compose child: daemon off, no leaked vars."""
        env = dict(os.environ)
        env["DOCKER_HOST"] = "unix:///nonexistent.sock"
        # Any COMPOSE_* variable of the developer's shell could change the
        # output, and the shim variables would redirect the normalizer.
        for name in [
            name
            for name in env
            if name.startswith("COMPOSE_")
            or name.startswith("CONTAINER_DOCKER_SHIM_")
        ]:
            del env[name]
        return env

    @staticmethod
    def _run(cwd: Optional[str]) -> Callable[..., subprocess.CompletedProcess]:
        """A ``run`` for :func:`load_normalized` in ``cwd`` (a no-op daemon)."""

        def run(argv: list, env: Mapping) -> subprocess.CompletedProcess:
            return subprocess.run(
                argv,
                env=env,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=60,
            )

        return run

    def _shim(
        self, argv: list, env: dict, cwd: str
    ) -> subprocess.CompletedProcess:
        """Run the shim's ``compose`` entry point on ``argv``."""
        return subprocess.run(
            [sys.executable, str(DOCKER), "compose", *argv],
            env=env,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=60,
        )

    def _direct(
        self, argv: list, env: dict, cwd: str
    ) -> subprocess.CompletedProcess:
        """Run the official binary on ``argv``."""
        return subprocess.run(
            [self.executable, *argv],
            env=env,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=60,
        )

    def _assert_same_model(
        self, argv: list, *, env: Optional[dict] = None, cwd: Optional[str] = None
    ) -> dict:
        """The shim and the official binary must load the same model.

        ``argv`` is the global flags plus ``config``; the official run gets
        ``--format json`` appended. Returns the model the shim loaded.
        """
        env = dict(self.env if env is None else env)
        cwd = cwd or self.project_dir
        shim = self._shim(argv, env, cwd)
        direct = self._direct(argv + ["--format", "json"], env, cwd)
        self.assertEqual(shim.returncode, 0, msg=shim.stderr)
        self.assertEqual(direct.returncode, 0, msg=direct.stderr)
        model = json.loads(shim.stdout)
        self.assertEqual(model, json.loads(direct.stdout))
        return model

    def _converted(self, model: dict) -> dict:
        """The model ``compose.py`` reads: the official JSON, converted."""
        return compose_normalizer.convert_model(model, SUPPORTED_VERSION)

    def _resolved(self, model: dict) -> tuple:
        """The build secrets ``compose_build_secrets`` resolves from the model."""
        service = model["services"]["app"]
        return compose_build_secrets.resolve(
            _Named("app"),
            service["build"]["secrets"],
            model["secrets"],
            service["build"]["context"],
        )

    def _expected_file(self) -> str:
        """Where the relative ``./secret.txt`` lands: the project directory."""
        return os.path.realpath(os.path.join(self.project_dir, "secret.txt"))

    def test_file_and_environment_secrets_resolve(self) -> None:
        """The file secret resolves to an absolute path; the env secret to its variable.

        The renamed entry's id is its target, and the secret no build
        references is absent from the model the shim reads.
        """
        model = self._assert_same_model(["-p", BUILD_SECRETS_PROJECT, "config"])
        converted = self._converted(model)
        self.assertEqual(sorted(converted["secrets"]), ["envsecret", "filesecret"])
        self.assertNotIn("unused", converted["secrets"])
        secrets, problems, notices = self._resolved(converted)
        self.assertEqual(problems, [])
        self.assertEqual(notices, [])
        self.assertEqual(
            secrets,
            [
                BuildSecret(id="filesecret", kind="file", value=self._expected_file()),
                BuildSecret(id="renamed", kind="env", value="BUILD_SECRET_VAR"),
            ],
        )
        self.assertTrue(os.path.isabs(secrets[0].value))

    def test_relative_file_resolves_with_and_without_project_directory(self) -> None:
        """Run from a different cwd: the relative file still lands in the project.

        Without ``--project-directory`` the compose file is named with a
        relative ``-f``; with it, the project directory is named. Both
        resolve ``./secret.txt`` against the project directory.
        """
        for argv in (
            ["-p", BUILD_SECRETS_PROJECT, "-f", "project/compose.yaml", "config"],
            ["-p", BUILD_SECRETS_PROJECT, "--project-directory", "project", "config"],
        ):
            with self.subTest(argv=argv):
                model = self._assert_same_model(argv, cwd=str(self._temp_root))
                secrets, problems, notices = self._resolved(self._converted(model))
                self.assertEqual(problems, [])
                self.assertEqual(notices, [])
                self.assertEqual(len(secrets), 2)
                self.assertEqual(secrets[0].value, self._expected_file())

    def test_undefined_secret_rejected_by_official_binary(self) -> None:
        """A build secret that is not defined: the official binary rejects the project.

        A temporary copy of the project with an unknown source; ``config``
        exits non-zero naming it, and the shim propagates the same failure.
        """
        bad = self._temp_root / "bad"
        shutil.copytree(BUILD_SECRETS_FIXTURE, bad)
        text = (bad / "compose.yaml").read_text(encoding="utf-8")
        text = text.replace("- filesecret\n", "- nosuch\n")
        self.assertIn("- nosuch", text)
        (bad / "compose.yaml").write_text(text, encoding="utf-8")
        direct = self._direct(
            ["-p", BUILD_SECRETS_PROJECT, "config"], self.env, str(bad)
        )
        self.assertNotEqual(direct.returncode, 0, msg=direct.stderr)
        self.assertIn("nosuch", direct.stderr)
        shim = self._shim(["-p", BUILD_SECRETS_PROJECT, "config"], self.env, str(bad))
        self.assertEqual(shim.returncode, direct.returncode, msg=shim.stderr)
        self.assertIn("nosuch", shim.stderr)

    def test_runtime_secret_refused_by_gate(self) -> None:
        """The runtime secret the service mounts is refused, not waived.

        The loaded model validates to a refusal for the service's ``secrets``
        key even when named in the waiver variable, and ``bin/docker compose
        up`` exits 64 printing the REFUSED line before any runtime call.
        """
        model = self._assert_same_model(["-p", BUILD_SECRETS_PROJECT, "config"])
        report = compose_capabilities.validate(model, list(model["services"].items()), {})
        self.assertEqual(
            [(f.service, f.key) for f in report.refusals], [("app", "secrets")]
        )
        self.assertIn("Compose secrets", report.refusals[0].message)
        waived = compose_capabilities.validate(
            model,
            list(model["services"].items()),
            {security.ENV_ALLOW: "secrets"},
        )
        self.assertEqual(
            [(f.service, f.key) for f in waived.refusals], [("app", "secrets")]
        )
        up = self._shim(["-p", BUILD_SECRETS_PROJECT, "up"], self.env, self.project_dir)
        self.assertEqual(up.returncode, 64, msg=up.stderr)
        self.assertIn("REFUSED", up.stderr)
        self.assertIn("secrets", up.stderr)


if __name__ == "__main__":
    unittest.main()
