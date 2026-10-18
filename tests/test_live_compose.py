"""Live compose tests (opt-in "live" tier) for the `docker compose` subset.

These tests run the real shim (``bin/docker``) against the real Apple
``container`` runtime and drive a real two-service project — an API that
reaches PostgreSQL over the project network, gated by
``depends_on: service_healthy`` — through a full ``up`` / ``down`` /
``up`` cycle, proving the named volume survives ``down``. The whole class
is skipped unless:

* ``SHIM_TIERS`` contains ``live`` (comma-separated list),
* ``container system status`` exits 0 and reports a running apiserver at
  the supported version,
* the ``postgres:17`` and ``alpine:3.20`` images are already available
  locally (never pulled).

Every project is named ``shimtest-cmp-<short uuid>`` and lives in a
throwaway ``tempfile.TemporaryDirectory`` holding its
``docker-compose.yml``; it is torn down with
``compose -p <project> -f <file> down -v`` in ``addCleanup`` for that
project only — the suite never prunes or matches by pattern.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim.cli import SUPPORTED_CONTAINER_VERSION  # noqa: E402

SHIM = ROOT / "bin" / "docker"
NAME_PREFIX = "shimtest-cmp-"
REQUIRED_IMAGES = (("postgres", "17"), ("alpine", "3.20"))
TIMEOUT_SECONDS = 120
UP_TIMEOUT_SECONDS = 300


def _run(
    args: list[str],
    timeout: int = TIMEOUT_SECONDS,
    cwd: Optional[Path] = None,
) -> subprocess.CompletedProcess:
    """Run a subprocess with captured output; ordinary live calls use 120 s."""
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, cwd=cwd
    )


def _image_available(listing: str, repository: str, tag: str) -> bool:
    """True when the `container image ls` table has a row for repo + tag."""
    return any(repository in line and tag in line for line in listing.splitlines())


class LiveComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        tiers = {
            tier.strip()
            for tier in os.environ.get("SHIM_TIERS", "").split(",")
            if tier.strip()
        }
        if "live" not in tiers:
            raise unittest.SkipTest(
                "live tier disabled: set SHIM_TIERS=live to run live compose tests"
            )
        try:
            status = _run(["container", "system", "status"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise unittest.SkipTest(f"container system status failed: {exc}") from exc
        output = (status.stdout + status.stderr).strip()
        if status.returncode != 0:
            raise unittest.SkipTest(
                f"container system status exited {status.returncode}: {output}"
            )
        if "running" not in output:
            raise unittest.SkipTest(f"container apiserver is not running: {output}")
        if SUPPORTED_CONTAINER_VERSION not in output:
            raise unittest.SkipTest(
                f"container system status does not report the supported "
                f"version {SUPPORTED_CONTAINER_VERSION}: {output}"
            )
        try:
            images = _run(["container", "image", "ls"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise unittest.SkipTest(f"container image ls failed: {exc}") from exc
        listing = images.stdout + images.stderr
        missing = [
            f"{repository}:{tag}"
            for repository, tag in REQUIRED_IMAGES
            if images.returncode != 0 or not _image_available(listing, repository, tag)
        ]
        if missing:
            raise unittest.SkipTest(f"images {', '.join(missing)} not available locally")

    # -- helpers ---------------------------------------------------------

    def _compose_text(self) -> str:
        """API + PostgreSQL project: db gated by a healthcheck, api waits for it."""
        return (
            "services:\n"
            "  db:\n"
            "    image: postgres:17\n"
            "    environment:\n"
            "      POSTGRES_PASSWORD: secret\n"
            "      PGDATA: /var/lib/postgresql/data/pgdata\n"
            "    volumes:\n"
            "      - pgdata:/var/lib/postgresql/data\n"
            "    healthcheck:\n"
            '      test: ["CMD-SHELL", "pg_isready -U postgres"]\n'
            "      interval: 2s\n"
            "      timeout: 5s\n"
            "      retries: 30\n"
            "      start_period: 5s\n"
            "  api:\n"
            "    image: postgres:17\n"
            '    command: ["sleep", "infinity"]\n'
            "    environment:\n"
            "      PGPASSWORD: secret\n"
            "    depends_on:\n"
            "      db:\n"
            "        condition: service_healthy\n"
            "volumes:\n"
            "  pgdata: {}\n"
        )

    def _open_project(self, compose_text: Optional[str] = None) -> None:
        """Fresh unique project in a temp dir holding its docker-compose.yml."""
        self._project = f"{NAME_PREFIX}{uuid.uuid4().hex[:8]}"
        self._tmp = tempfile.TemporaryDirectory(prefix=NAME_PREFIX)
        self.addCleanup(self._tmp.cleanup)
        workdir = Path(self._tmp.name)
        self._compose_file = workdir / "docker-compose.yml"
        self._compose_file.write_text(compose_text if compose_text is not None else self._compose_text())
        self.addCleanup(self._cleanup_project, self._project, self._compose_file, workdir)

    def _compose(self, *args: str, timeout: int = TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
        """`bin/docker compose` for this project, run from the temp dir."""
        return _run(
            [
                sys.executable,
                str(SHIM),
                "compose",
                "-p",
                self._project,
                "-f",
                str(self._compose_file),
                *args,
            ],
            timeout=timeout,
            cwd=Path(self._tmp.name),
        )

    def _cleanup_project(self, project: str, compose_file: Path, workdir: Path) -> None:
        """Tear down exactly this project; errors are ignored, never prune."""
        try:
            _run(
                [
                    sys.executable,
                    str(SHIM),
                    "compose",
                    "-p",
                    project,
                    "-f",
                    str(compose_file),
                    "down",
                    "-v",
                ],
                cwd=workdir,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    # -- tests -----------------------------------------------------------

    def test_api_reaches_postgres_and_data_survives_down_and_up(self) -> None:
        self._open_project()
        project = self._project

        # 1. Detached up: the api is created only after the db reports healthy.
        result = self._compose("up", "-d", "--wait-timeout", "120", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # 2. The api reaches the db over the project network and writes a row.
        result = self._compose(
            "exec",
            "-T",
            "api",
            "psql",
            "-h", "db",
            "-U", "postgres",
            "-v", "ON_ERROR_STOP=1",
            "-c",
            "CREATE TABLE IF NOT EXISTS notes(id serial primary key, body text); "
            "INSERT INTO notes(body) VALUES ('hello from api');",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # 3. `down` removes exactly this project's containers.
        result = self._compose("down")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        listing = _run(["container", "ls", "-a"])
        self.assertNotIn(project, listing.stdout + listing.stderr)

        # 4. `up` again: the named volume survived `down`, so the row is back.
        result = self._compose("up", "-d", "--wait-timeout", "120", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self._compose(
            "exec",
            "-T",
            "api",
            "psql",
            "-h", "db",
            "-U", "postgres",
            "-t", "-A",
            "-c",
            "SELECT body FROM notes ORDER BY id",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("hello from api", result.stdout)

        # 5. `down -v` also removes the project's named volume.
        result = self._compose("down", "-v")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        containers = _run(["container", "ls", "-a"])
        self.assertNotIn(project, containers.stdout + containers.stderr)
        volumes = _run(["container", "volume", "ls"])
        self.assertNotIn(project, volumes.stdout + volumes.stderr)

    def test_restart_db_keeps_api_connectivity(self) -> None:
        self._open_project()

        result = self._compose("up", "-d", "--wait-timeout", "120", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        result = self._compose(
            "exec",
            "-T",
            "api",
            "psql",
            "-h", "db",
            "-U", "postgres",
            "-t", "-A",
            "-c",
            "SELECT 1",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1", result.stdout)

        result = self._compose("restart", "db")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # The db needs a moment to accept connections after the restart.
        result = self._compose(
            "exec",
            "-T",
            "api",
            "psql",
            "-h", "db",
            "-U", "postgres",
            "-t", "-A",
            "-c",
            "SELECT 1",
        )
        attempts = 0
        while result.returncode != 0 and attempts < 15:
            time.sleep(2)
            attempts += 1
            result = self._compose(
                "exec",
                "-T",
                "api",
                "psql",
                "-h", "db",
                "-U", "postgres",
                "-t", "-A",
                "-c",
                "SELECT 1",
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1", result.stdout)

    def test_dependent_waits_for_delayed_healthy_dependency(self) -> None:
        self._open_project(
            "services:\n"
            "  slow:\n"
            "    image: alpine:3.20\n"
            '    command: ["sh", "-c", "sleep 5; touch /tmp/ready; sleep infinity"]\n'
            "    healthcheck:\n"
            '      test: ["CMD-SHELL", "test -f /tmp/ready"]\n'
            "      interval: 1s\n"
            "      timeout: 3s\n"
            "      retries: 30\n"
            "      start_period: 0s\n"
            "  app:\n"
            "    image: alpine:3.20\n"
            '    command: ["sleep", "infinity"]\n'
            "    depends_on:\n"
            "      slow:\n"
            "        condition: service_healthy\n"
        )

        started = time.monotonic()
        result = self._compose("up", "-d", "--wait-timeout", "120", timeout=UP_TIMEOUT_SECONDS)
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # The app could only start after `slow` passed its delayed healthcheck.
        self.assertGreaterEqual(elapsed, 4.0)

        result = self._compose("ps")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("slow", result.stdout)
        self.assertIn("app", result.stdout)

    def test_never_healthy_dependency_hits_wait_timeout(self) -> None:
        self._open_project(
            "services:\n"
            "  stuck:\n"
            "    image: alpine:3.20\n"
            '    command: ["sleep", "infinity"]\n'
            "    healthcheck:\n"
            '      test: ["CMD-SHELL", "false"]\n'
            "      interval: 1s\n"
            "      timeout: 3s\n"
            "      retries: 1000\n"
            "      start_period: 0s\n"
            "  app:\n"
            "    image: alpine:3.20\n"
            '    command: ["sleep", "infinity"]\n'
            "    depends_on:\n"
            "      stuck:\n"
            "        condition: service_healthy\n"
        )

        result = self._compose("up", "-d", "--wait-timeout", "8", timeout=UP_TIMEOUT_SECONDS)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("stuck", result.stdout + result.stderr)

        listing = _run(["container", "ls", "-a"])
        self.assertNotIn(f"{self._project}-app", listing.stdout + listing.stderr)


if __name__ == "__main__":
    unittest.main()
