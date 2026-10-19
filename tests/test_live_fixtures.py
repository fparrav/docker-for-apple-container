"""Live compat-fixture tests (opt-in "live" tier) for the `docker compose` subset.

These tests run the real shim (``bin/docker``) against the real Apple
``container`` runtime and drive the shared compat fixtures under
``tests/fixtures/compat/`` through a full ``up`` / ``down`` cycle. The
fixtures are the same files the conformance tier will use, copied unmodified
(header comment lines included). Every test method is gated with
``tiers.live_tier``: it is skipped unless ``SHIM_TIERS`` contains ``live`` and
the supported ``container`` runtime is usable, and the class skips
additionally when a fixture's pinned image is not already available locally
(``require_images``; never pulled).

Each fixture is copied into a throwaway temp directory by ``FixtureHarness``
and driven as a fresh project named ``shimtest-fx-<short uuid>`` through
``tiers.disposable_name``; teardown runs ``compose -p <project> down -v`` in
``addCleanup`` for that project only — the suite never prunes or matches by
pattern. ``CONTAINER_DOCKER_SHIM_CONTAINER`` is honoured exactly as the
caller set it, never defaulted.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import tiers  # noqa: E402

TIER = "live"

FIXTURES_DIR = ROOT / "tests" / "fixtures" / "compat"
SHIM = ROOT / "bin" / "docker"
NAME_PREFIX = "shimtest-fx-"
FIXTURE_A = "a-nginx"
FIXTURE_B = "b-api-postgres"
FIXTURE_C = "c-api-postgres-redis"
FIXTURE_D = "d-health-deps"
FIXTURE_E = "e-networks-volumes"
FIXTURE_H = "h-dotenv"
FIXTURE_I = "i-multiple-files"
FIXTURE_J = "j-security"
FIXTURE_K = "k-short-lived"
FIXTURE_M = "m-readonly-volume"
FIXTURE_O = "o-external-volume"
FIXTURE_P = "p-selective-up"
FIXTURE_N = "n-network-none"
FIXTURE_L = "l-partial-failure"
TIMEOUT_SECONDS = 120
UP_TIMEOUT_SECONDS = 300
HTTP_WAIT_SECONDS = 30
POSTGRES_WAIT_SECONDS = 60
STATE_WAIT_SECONDS = 30
HTTP_URL = "http://127.0.0.1:8080/"


def _run(
    args: list[str],
    timeout: int = TIMEOUT_SECONDS,
    cwd: Optional[Path] = None,
) -> subprocess.CompletedProcess:
    """Run a subprocess with captured output; ordinary live calls use 120 s.

    The caller's environment is passed through as-is, so
    ``CONTAINER_DOCKER_SHIM_CONTAINER`` reaches the shim only when the caller
    has already set it.
    """
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=os.environ.copy()
    )


def _container() -> str:
    """The Apple ``container`` CLI: the caller's ``CONTAINER_DOCKER_SHIM_CONTAINER`` if set, else ``container`` on PATH."""
    return os.environ.get(tiers.CONTAINER_BIN_ENV) or "container"


def _started_date(container_name: str) -> datetime:
    """The ``startedDate`` of one container, read from ``container inspect``.

    The Apple runtime prints a JSON array whose first element carries
    ``status.startedDate`` as an ISO string. A trailing ``Z`` is normalised
    to ``+00:00`` because Python 3.9's ``fromisoformat`` cannot parse it, and
    a naive timestamp is assumed to be UTC.
    """
    listing = _run([_container(), "inspect", container_name])
    if listing.returncode != 0:
        raise AssertionError(
            f"inspect {container_name} exited {listing.returncode}: "
            f"{listing.stdout + listing.stderr}"
        )
    try:
        raw = json.loads(listing.stdout)[0]["status"]["startedDate"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AssertionError(
            f"inspect {container_name} returned no startedDate: {listing.stdout}"
        ) from exc
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _creation_identity(container_name: str) -> tuple[str, str]:
    """``(startedDate, creationDate)`` of one container, from ``container inspect``.

    The Apple runtime prints a JSON array whose first element carries
    ``status.startedDate`` (moves on every start) and
    ``configuration.creationDate`` (set once at creation). The pair pins the
    container's creation identity: a recreated container changes it.
    """
    listing = _run([_container(), "inspect", container_name])
    if listing.returncode != 0:
        raise AssertionError(
            f"inspect {container_name} exited {listing.returncode}: "
            f"{listing.stdout + listing.stderr}"
        )
    try:
        item = json.loads(listing.stdout)[0]
        started = (item.get("status") or {}).get("startedDate")
        created = (item.get("configuration") or {}).get("creationDate")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AssertionError(
            f"inspect {container_name} returned no identity: {listing.stdout}"
        ) from exc
    return str(started), str(created)


def _image_available(listing: str, repository: str, tag: str) -> bool:
    """True when the `container image ls` table has a row for repo + tag."""
    return any(repository in line and tag in line for line in listing.splitlines())


def copy_fixture(
    letter_dir: str,
    overrides: Optional[Mapping[str, str]] = None,
) -> Path:
    """Copy ``tests/fixtures/compat/<letter_dir>/`` into a fresh temp dir.

    The fixture's two header lines (``# tiers:`` and ``# images:``) are plain
    YAML comments, so the files are copied as-is, unmodified: the live and
    conformance tiers must use the identical file. ``overrides`` maps
    relative paths to replacement text written on top of the copy.
    """
    source = FIXTURES_DIR / letter_dir
    if not source.is_dir():
        raise unittest.SkipTest(f"fixture {letter_dir} not found under {FIXTURES_DIR}")
    workdir = Path(tempfile.mkdtemp(prefix=NAME_PREFIX))
    shutil.copytree(source, workdir, dirs_exist_ok=True)
    for relative, content in (overrides or {}).items():
        target = workdir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return workdir


def _files_args(*names: str) -> tuple[str, ...]:
    """Flatten compose file names into repeated ``-f FILE`` flags.

    ``FixtureHarness.compose`` puts ``-p <project>`` first; the shim's
    global-flag loop accepts ``-f`` before or after ``-p`` alike, so the
    flags may follow the project name.
    """
    args: list[str] = []
    for name in names:
        args.extend(["-f", name])
    return tuple(args)


class FixtureHarness:
    """One compat fixture in a throwaway temp dir, driven through the shim.

    The project name is a unique ``shimtest-fx-<short uuid>`` and two
    cleanups are registered on the owning test (in reverse order): tear the
    project down with ``compose -p <project> down -v`` — never prune, never
    match by pattern — and remove the copied fixture directory.
    """

    def __init__(
        self,
        test: unittest.TestCase,
        letter_dir: str,
        overrides: Optional[Mapping[str, str]] = None,
        docker: Optional[str] = None,
        prefix: Optional[str] = None,
    ) -> None:
        """``docker`` is the client to drive (default: the shim); the
        conformance tier passes a real Docker Engine client. ``prefix``
        overrides the disposable-name topic (default ``fx``)."""
        self._test = test
        self._docker = docker or str(SHIM)
        self._workdir = copy_fixture(letter_dir, overrides)
        self.project = tiers.disposable_name(prefix or "fx")
        test.addCleanup(self._cleanup)

    @property
    def workdir(self) -> Path:
        """The temp dir holding the copied fixture files (compose's cwd)."""
        return self._workdir

    def compose(
        self, *args: str, timeout: int = TIMEOUT_SECONDS
    ) -> subprocess.CompletedProcess:
        """``bin/docker compose -p <project> ...`` run from the fixture dir."""
        return _run(
            [self._docker, "compose", "-p", self.project, *args],
            timeout=timeout,
            cwd=self._workdir,
        )

    def _cleanup(self) -> None:
        """Tear down exactly this project, then remove the temp dir; ignore errors."""
        try:
            self.compose("down", "-v")
        except (OSError, subprocess.SubprocessError):
            pass
        shutil.rmtree(self._workdir, ignore_errors=True)


class LiveFixtureTests(unittest.TestCase):
    def require_images(self, *references: str) -> None:
        """Skip the test unless every ``repository:tag`` is already local (never pulled)."""
        try:
            images = _run([_container(), "image", "ls"])
        except (OSError, subprocess.SubprocessError) as exc:
            self.skipTest(f"container image ls failed: {exc}")
        listing = images.stdout + images.stderr
        missing = [
            reference
            for reference in references
            if images.returncode != 0
            or not _image_available(listing, *reference.split(":", 1))
        ]
        if missing:
            self.skipTest(f"images {', '.join(missing)} not available locally")

    # -- helpers ---------------------------------------------------------

    def _wait_for_http_200(self, url: str, wait_seconds: float = HTTP_WAIT_SECONDS) -> None:
        """Poll ``url`` with urllib until it answers HTTP 200, or fail after the deadline."""
        deadline = time.monotonic() + wait_seconds
        last_error = "not attempted"
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    if response.code == 200:
                        return
                    last_error = f"HTTP {response.code} from {url}"
            except OSError as exc:
                last_error = f"{url} unreachable: {exc}"
            time.sleep(1)
        self.fail(f"{url} did not answer HTTP 200 within {wait_seconds:.0f}s: {last_error}")

    def _wait_for_postgres_ready(self, harness: FixtureHarness, wait_seconds: float = POSTGRES_WAIT_SECONDS) -> None:
        """Poll ``db`` with ``pg_isready`` until it accepts connections, or fail after the deadline."""
        result = harness.compose("exec", "-T", "db", "pg_isready", "-U", "postgres")
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline and result.returncode != 0:
            time.sleep(2)
            result = harness.compose("exec", "-T", "db", "pg_isready", "-U", "postgres")
        self.assertEqual(
            result.returncode,
            0,
            f"db was not ready within {wait_seconds:.0f}s: {result.stdout + result.stderr}",
        )

    def _wait_for_state(
        self,
        harness: FixtureHarness,
        service: str,
        want: str,
        wait_seconds: float = STATE_WAIT_SECONDS,
    ) -> str:
        """Poll ``compose ps -a`` until the service's row shows ``want`` in its state.

        The Apple runtime updates a container's state when the process ends;
        a one-shot ``ps`` right after ``up`` can still show the old state.
        """
        deadline = time.monotonic() + wait_seconds
        last = ""
        while time.monotonic() < deadline:
            result = harness.compose("ps", "-a")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for row in result.stdout.splitlines():
                columns = row.split("\t")
                if len(columns) >= 4 and columns[3] == service and want in columns[2]:
                    return row
            last = result.stdout
            time.sleep(1)
        self.fail(
            f"compose ps -a never showed state {want!r} for service {service!r} "
            f"within {wait_seconds:.0f}s:\n{last}"
        )

    def _wait_for_log(
        self,
        harness: FixtureHarness,
        service: str,
        needle: str,
        wait_seconds: float = 20.0,
    ) -> None:
        """Poll ``compose logs <service>`` until ``needle`` appears in the output, or fail after the deadline.

        For a single service the shim passes straight through to
        ``container logs``, so the container's printed line is the raw
        output (no service prefix, no colour); a one-shot logs call right
        after ``up`` can miss the line, hence the poll.
        """
        deadline = time.monotonic() + wait_seconds
        last = ""
        while time.monotonic() < deadline:
            result = harness.compose("logs", service)
            if result.returncode == 0 and needle in result.stdout:
                return
            last = result.stdout + result.stderr
            time.sleep(1)
        self.fail(
            f"compose logs {service} never showed {needle!r} within "
            f"{wait_seconds:.0f}s:\n{last}"
        )

    # -- tests -----------------------------------------------------------

    @tiers.live_tier()
    def test_fixture_a_published_port_answers_http_and_down_removes_everything(self) -> None:
        self.require_images("nginx:1.27-alpine")
        harness = FixtureHarness(self, FIXTURE_A)
        project = harness.project

        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        self._wait_for_http_200(HTTP_URL)

        result = harness.compose("down")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        listing = _run([_container(), "ls", "--all"])
        self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
        self.assertNotIn(project, listing.stdout + listing.stderr)

    @tiers.live_tier()
    def test_fixture_b_postgres_row_survives_down_and_disappears_with_down_v(self) -> None:
        self.require_images("alpine:3.20", "postgres:17")
        harness = FixtureHarness(self, FIXTURE_B)
        project = harness.project

        # 1. Detached up, then wait for postgres to accept connections.
        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self._wait_for_postgres_ready(harness)

        # 2. Write a row into the data directory on the project's named volume.
        result = harness.compose(
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "CREATE TABLE IF NOT EXISTS notes(id serial primary key, body text); "
            "INSERT INTO notes(body) VALUES ('survives down');",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # 3. `down` without `-v` keeps the project's named volume.
        result = harness.compose("down")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        volumes = _run([_container(), "volume", "ls"])
        self.assertEqual(volumes.returncode, 0, volumes.stdout + volumes.stderr)
        self.assertIn(project, volumes.stdout + volumes.stderr)

        # 4. `up` again: the volume survived `down`, so the row is back.
        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self._wait_for_postgres_ready(harness)
        result = harness.compose(
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "postgres",
            "-t",
            "-A",
            "-c",
            "SELECT body FROM notes ORDER BY id",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("survives down", result.stdout)

        # 5. `down -v` also removes the project's named volume.
        result = harness.compose("down", "-v")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        volumes = _run([_container(), "volume", "ls"])
        self.assertEqual(volumes.returncode, 0, volumes.stdout + volumes.stderr)
        self.assertNotIn(project, volumes.stdout + volumes.stderr)

    @tiers.live_tier()
    def test_fixture_o_external_volume_survives_down_v_and_is_reused(self) -> None:
        self.require_images("postgres:17")

        # 0. Pre-create the external volume the fixture declares (its name
        #    arrives through a `.env` override); a cleanup removes it by the
        #    exact name — `down -v` must not remove it.
        ext = tiers.disposable_name("ext")

        def _remove_external_volume(name: str) -> None:
            # Ignore errors: the volume may already be gone.
            _run([_container(), "volume", "rm", name])

        # Registered before the create so a failed create is still cleaned up.
        self.addCleanup(_remove_external_volume, ext)
        result = _run([_container(), "volume", "create", ext])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        harness = FixtureHarness(
            self, FIXTURE_O, overrides={".env": f"EXT_VOLUME={ext}\n"}
        )
        project = harness.project

        # 1. Detached up, then wait for postgres to accept connections.
        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self._wait_for_postgres_ready(harness)

        # 2. Write a row into the data directory on the external volume.
        result = harness.compose(
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "CREATE TABLE IF NOT EXISTS notes(id serial primary key, body text); "
            "INSERT INTO notes(body) VALUES ('survives down -v');",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # 3. `down -v` must not remove the external volume: it is not the
        #    project's, so it is still listed afterwards.
        result = harness.compose("down", "-v")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        volumes = _run([_container(), "volume", "ls"])
        self.assertEqual(volumes.returncode, 0, volumes.stdout + volumes.stderr)
        self.assertIn(ext, volumes.stdout + volumes.stderr)

        # 4. `up` again reuses the same external volume, so the row is back.
        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self._wait_for_postgres_ready(harness)
        result = harness.compose(
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "postgres",
            "-t",
            "-A",
            "-c",
            "SELECT body FROM notes ORDER BY id",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("survives down -v", result.stdout)

        # 5. The project created no volume of its own for the declaration:
        #    neither the key-prefixed nor the name-prefixed copy exists.
        volumes = _run([_container(), "volume", "ls"])
        self.assertEqual(volumes.returncode, 0, volumes.stdout + volumes.stderr)
        listing = volumes.stdout + volumes.stderr
        self.assertNotIn(f"{project}_pgext", listing)
        self.assertNotIn(f"{project}_{ext}", listing)

    @tiers.live_tier()
    def test_fixture_p_up_service_creates_its_dependency_and_not_unrelated_services(self) -> None:
        self.require_images("alpine:3.20")
        harness = FixtureHarness(self, FIXTURE_P)
        project = harness.project

        # 1. `up -d api` pulls in the named service's dependency closure: db
        #    joins, the unrelated worker does not; the up blocks until db is
        #    healthy, so its Creating line precedes api's in the output.
        result = harness.compose("up", "-d", "api", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = result.stdout + result.stderr
        db_creating = output.find(f"Creating {project}-db-1")
        api_creating = output.find(f"Creating {project}-api-1")
        self.assertNotEqual(db_creating, -1, f"no Creating line for db: {output}")
        self.assertNotEqual(api_creating, -1, f"no Creating line for api: {output}")
        self.assertLess(
            db_creating,
            api_creating,
            f"db was not created before api: {output}",
        )
        # The shim reports the healthcheck outcome before it creates api.
        db_healthy = output.find(f"{project}-db-1 is healthy")
        self.assertNotEqual(db_healthy, -1, f"db was never reported healthy: {output}")
        self.assertLess(
            db_healthy,
            api_creating,
            f"api was created before db was healthy: {output}",
        )

        # 2. The container listing proves the selection: db and api exist,
        #    the unrelated worker was never created.
        listing = _run([_container(), "ls", "--all"])
        self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
        names = listing.stdout + listing.stderr
        self.assertIn(f"{project}-db-1", names)
        self.assertIn(f"{project}-api-1", names)
        self.assertNotIn(f"{project}-worker-1", names)

    @tiers.live_tier()
    def test_fixture_p_no_deps_creates_only_the_named_service(self) -> None:
        self.require_images("alpine:3.20")
        harness = FixtureHarness(self, FIXTURE_P)
        project = harness.project

        # 1. `--no-deps` up acts on the named service alone: api comes up,
        #    its db dependency is skipped with a notice that names the flag,
        #    and worker is untouched.
        result = harness.compose(
            "up", "-d", "--no-deps", "api", timeout=UP_TIMEOUT_SECONDS
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("--no-deps", result.stderr, result.stdout + result.stderr)

        # 2. The container listing proves nothing else of the project exists.
        listing = _run([_container(), "ls", "--all"])
        self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
        names = listing.stdout + listing.stderr
        self.assertIn(f"{project}-api-1", names)
        self.assertNotIn(f"{project}-db-1", names)
        self.assertNotIn(f"{project}-worker-1", names)

    @tiers.live_tier()
    def test_fixture_c_service_names_resolve_inside_the_api_container(self) -> None:
        self.require_images("alpine:3.20", "postgres:17", "redis:7-alpine")
        harness = FixtureHarness(self, FIXTURE_C)

        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # The fixture defines the services `db` and `cache` (not `redis`);
        # `api` must resolve both names over the project network. busybox in
        # alpine:3.20 ships `getent`, so no nslookup/ping fallback is needed.
        for service in ("db", "cache"):
            result = harness.compose("exec", "-T", "api", "getent", "hosts", service)
            self.assertEqual(
                result.returncode,
                0,
                f"getent hosts {service} inside api: {result.stdout + result.stderr}",
            )
            self.assertTrue(
                result.stdout.strip(),
                f"getent hosts {service} inside api resolved to no rows",
            )

    @tiers.live_tier()
    def test_fixture_d_api_starts_only_after_db_is_healthy(self) -> None:
        self.require_images("alpine:3.20", "postgres:17")
        harness = FixtureHarness(self, FIXTURE_D)
        project = harness.project

        # 1. `up --wait` blocks until the whole graph is running; api only
        #    joins once db's healthcheck has passed, so record the wall time
        #    before the call to bound the ordering assertions below.
        before = datetime.now(timezone.utc)
        result = harness.compose("up", "-d", "--wait-timeout", "120", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # 2. Both services must be listed by `compose ps` after the up.
        listing = harness.compose("ps")
        self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
        for service in ("db", "api"):
            self.assertIn(service, listing.stdout, listing.stdout)

        # 3. Container start dates prove the ordering: db was started first,
        #    api only after, and api only while the up call was still blocked.
        db_started = _started_date(f"{project}-db-1")
        api_started = _started_date(f"{project}-api-1")
        self.assertLessEqual(
            db_started,
            api_started,
            f"api started before db: db={db_started.isoformat()} api={api_started.isoformat()}",
        )
        self.assertGreaterEqual(api_started, before)

        # 4. Right after the up returns, the database already accepts
        #    connections: the api was only started once db was healthy.
        result = harness.compose("exec", "-T", "db", "pg_isready", "-U", "postgres")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @tiers.live_tier()
    def test_fixture_e_services_resolve_only_peers_on_a_shared_network(self) -> None:
        self.require_images("alpine:3.20")
        harness = FixtureHarness(self, FIXTURE_E)

        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        def resolves(service: str, peer: str) -> bool:
            # busybox getent exits non-zero when the name is not found.
            result = harness.compose("exec", "-T", service, "getent", "hosts", peer)
            return result.returncode == 0 and bool(result.stdout.strip())

        # app joins both networks, so it sees the peer on each one.
        self.assertTrue(resolves("app", "edge"))
        self.assertTrue(resolves("app", "store"))
        # edge (front only) and store (back only) share no network, so
        # neither resolves the other, but both resolve app.
        self.assertTrue(resolves("edge", "app"))
        self.assertTrue(resolves("store", "app"))
        self.assertFalse(resolves("edge", "store"), "edge resolved store across networks")
        self.assertFalse(resolves("store", "edge"), "store resolved edge across networks")

    @tiers.live_tier()
    def test_fixture_e_bind_mounted_file_visible_in_container(self) -> None:
        self.require_images("alpine:3.20")
        harness = FixtureHarness(self, FIXTURE_E)

        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # ./conf is bind-mounted read-only at /etc/conf inside app.
        result = harness.compose("exec", "-T", "app", "cat", "/etc/conf/app.conf")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("greeting=hello", result.stdout)

    @tiers.live_tier()
    def test_fixture_j_up_refuses_privileged_and_creates_nothing(self) -> None:
        self.require_images("alpine:3.20")

        # A temporary copy of the hardened fixture asking for `privileged`,
        # which security.decide() must refuse before `up` creates anything.
        original = (FIXTURES_DIR / FIXTURE_J / "compose.yaml").read_text()
        privileged = original.replace(
            "    image: alpine:3.20\n",
            "    image: alpine:3.20\n    privileged: true\n",
            1,
        )
        self.assertNotEqual(privileged, original)

        harness = FixtureHarness(self, FIXTURE_J, overrides={"compose.yaml": privileged})
        project = harness.project

        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 64, result.stdout + result.stderr)
        self.assertIn("privileged", result.stderr, result.stdout + result.stderr)
        self.assertIn("hardened", result.stderr, result.stdout + result.stderr)

        listing = _run([_container(), "ls", "--all"])
        self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
        self.assertNotIn(project, listing.stdout + listing.stderr)

    @tiers.live_tier()
    def test_fixture_m_read_only_volume_and_bind_reject_writes(self) -> None:
        self.require_images("alpine:3.20")
        harness = FixtureHarness(self, FIXTURE_M)

        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # The service prints what its own write attempts did.
        self._wait_for_log(harness, "writer", "VOL_WRITE_BLOCKED")
        self._wait_for_log(harness, "writer", "BIND_WRITE_BLOCKED")

        for target in ("/vol/y", "/shared/y"):
            attempt = harness.compose("exec", "-T", "writer", "touch", target)
            self.assertNotEqual(attempt.returncode, 0, f"write to {target} succeeded")
            self.assertIn("Read-only file system", attempt.stdout + attempt.stderr)

    @tiers.live_tier()
    def test_fixture_n_network_mode_none_is_refused_and_creates_nothing(self) -> None:
        self.require_images("alpine:3.20")
        harness = FixtureHarness(self, FIXTURE_N)
        project = harness.project

        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 64, result.stdout + result.stderr)
        self.assertIn("REFUSED:", result.stderr, result.stderr)
        self.assertIn("isolated", result.stderr, result.stderr)
        self.assertIn("network_mode", result.stderr, result.stderr)

        # Nothing of this project exists: no container, network or volume.
        for kind in (["ls", "--all"], ["network", "ls"], ["volume", "ls"]):
            listing = _run([_container(), *kind])
            self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
            self.assertNotIn(project, listing.stdout + listing.stderr)

    @tiers.live_tier()
    def test_fixture_k_exited_service_reported_exited_and_exit_code_refused(self) -> None:
        self.require_images("alpine:3.20")
        harness = FixtureHarness(self, FIXTURE_K)
        project = harness.project

        # 1. Detached up succeeds: `container run -d` returns 0 at creation
        #    even though the service exits 3 milliseconds later, and `up -d`
        #    without --wait returns that code. A failure must name the service.
        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        if result.returncode != 0:
            self.assertIn("brief", result.stderr, result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # 2. `compose ps -a` reports the service as exited; the Apple runtime
        #    exposes no numeric exit-code field, so the state is the report.
        row = self._wait_for_state(harness, "brief", "exited")
        self.assertTrue("exited" in row or "3" in row, row)

        # 3. Docker's `inspect --format '{{.State.ExitCode}}'` names no field
        #    Apple container 1.5.0 exposes, so the shim refuses it (exit 64,
        #    unit-tested in tests/test_inspect.py) instead of inventing a value.
        inspect = _run(
            [str(SHIM), "inspect", "--format", "{{.State.ExitCode}}", f"{project}-brief-1"]
        )
        self.assertEqual(inspect.returncode, 64, inspect.stdout + inspect.stderr)
        self.assertEqual(inspect.stdout, "")
        self.assertIn("unsupported inspect field: .State.ExitCode", inspect.stderr)

    @tiers.live_tier()
    def test_fixture_l_partial_failure_leaves_healthy_service_and_second_up_converges(self) -> None:
        self.require_images("alpine:3.20", "nginx:1.27-alpine")
        harness = FixtureHarness(self, FIXTURE_L)
        project = harness.project
        web = f"{project}-web-1"
        flaky = f"{project}-flaky-1"

        # 1. `up -d` exits 0 even though `flaky` exits 1 immediately: like
        #    Docker without --wait, a one-shot service failing is not an up
        #    failure, and the healthy service must be left running.
        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # 2. `compose ps -a` (NAME IMAGE STATE SERVICE, tab separated) shows
        #    the split: web running, flaky exited.
        self._wait_for_state(harness, "web", "running")
        self._wait_for_state(harness, "flaky", "exited")

        # 3. Pin web's creation identity, then run a second `up -d`: it must
        #    report web "is up to date" (no recreate) and only restart flaky.
        before = _creation_identity(web)
        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = result.stdout + result.stderr
        self.assertIn(f"{web} is up to date", output, output)
        self.assertIn(f"Starting {flaky}", output, output)

        # 4. The unchanged identity proves web was not recreated, and flaky
        #    is reported exited again after the converged up.
        self.assertEqual(
            _creation_identity(web),
            before,
            "the second up recreated the healthy web container",
        )
        self._wait_for_state(harness, "flaky", "exited")

    @tiers.live_tier()
    def test_fixture_h_dotenv_value_reaches_container(self) -> None:
        self.require_images("alpine:3.20")
        # The fixture ships `dot-env`, the intended content of a project
        # `.env` file, but the copy has no `.env`; write it via an override
        # (the header comment lines are inert to the .env parser).
        env_file = (FIXTURES_DIR / FIXTURE_H / "dot-env").read_text()
        harness = FixtureHarness(self, FIXTURE_H, overrides={".env": env_file})

        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # greeter exits right after printing $GREETING, so the .env value
        # (which must win over the ${GREETING:-hello} default) is read back
        # from the container's logs.
        self._wait_for_log(harness, "greeter", "hi")

    @tiers.live_tier()
    def test_fixture_h_default_value_used_without_dotenv(self) -> None:
        self.require_images("alpine:3.20")
        harness = FixtureHarness(self, FIXTURE_H)

        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # With no `.env` the ${GREETING:-hello} interpolation falls back to
        # the default.
        self._wait_for_log(harness, "greeter", "hello")

    @tiers.live_tier()
    def test_fixture_i_three_files_merge_with_override_winning(self) -> None:
        self.require_images("alpine:3.20")
        harness = FixtureHarness(self, FIXTURE_I)
        files = _files_args("compose.yaml", "compose.override.yaml", "compose.extra.yaml")

        result = harness.compose(*files, "up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # `web` comes from the base + override files, `worker` only from the
        # extra file: all three files must be merged for the up to succeed.
        listing = harness.compose(*files, "ps")
        self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
        for service in ("web", "worker"):
            self.assertIn(service, listing.stdout, listing.stdout)

        # The override file sets LAYER on `web`; the env inside the running
        # container must carry the merged value.
        env = harness.compose(*files, "exec", "-T", "web", "env")
        self.assertEqual(env.returncode, 0, env.stdout + env.stderr)
        self.assertIn("LAYER=override", env.stdout)


if __name__ == "__main__":
    unittest.main()
