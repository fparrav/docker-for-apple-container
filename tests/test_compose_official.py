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

from container_docker_shim import compose_normalizer  # noqa: E402

TIER = "fake"


SUPPORTED_VERSION = "5.6.0"
PROJECT_NAME = "full"
FULL_FIXTURE = ROOT / "tests" / "fixtures" / "compose" / "full"
NORMALIZED_FIXTURE = ROOT / "tests" / "fixtures" / "compose" / "normalized" / "full-default.json"


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


if __name__ == "__main__":
    unittest.main()
