"""Conformance-tier compat fixture tests (opt-in "conformance" tier).

These tests run the same compat fixtures the live tier uses — the identical
files under ``tests/fixtures/compat/``, copied unmodified by the shared
``FixtureHarness`` from ``tests/test_live_fixtures.py`` — but drive them
through a real Docker Engine client named by ``SHIM_CONFORMANCE_DOCKER`` and
read at test time, instead of the shim against the Apple ``container``
runtime. Every test method is gated with ``tiers.conformance_tier``: it is
skipped unless ``SHIM_TIERS`` contains ``conformance``, the client variable
is set, and the real Engine answers ``info``. The gate never probes the
Apple ``container`` runtime, so the tier runs on a Docker-only host.

Each fixture is copied into a throwaway temp directory and driven as a fresh
project named ``shimtest-conf-<short uuid>``; teardown runs
``compose -p <project> down -v`` in ``addCleanup`` for that project only —
the suite never prunes or matches by pattern. Docker pulls images on demand,
so there is no ``require_images`` gate: the same fixture files must come up
on the real client without any local-image precondition.

The observable results asserted here are the same ones the live tier asserts
in ``tests/test_live_fixtures.py``, with the Apple-runtime-specific plumbing
replaced by Docker-native equivalents: ``docker ps -a --format
'{{.Names}}'`` instead of ``container ls``, ``docker volume ls --format
'{{.Name}}'`` instead of ``container volume ls``, ``docker inspect --format
'{{.State.StartedAt}}'`` instead of the runtime's ``startedDate`` JSON, and
raw ``docker logs <container>`` instead of ``compose logs <service>``.

Fixture K is the one behavioural divergence: the Apple runtime exposes no
numeric exit-code field, so the shim refuses ``--format
'{{.State.ExitCode}}'`` (exit 64, unit-tested in ``tests/test_inspect.py``),
while real Docker reports it — the conformance test asserts the numeric 3.
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import tiers  # noqa: E402

from test_live_fixtures import (  # noqa: E402
    FIXTURE_A,
    FIXTURE_B,
    FIXTURE_C,
    FIXTURE_D,
    FIXTURE_E,
    FIXTURE_H,
    FIXTURE_I,
    FIXTURE_K,
    FIXTURE_M,
    FIXTURE_N,
    FIXTURE_O,
    FIXTURE_P,
    FIXTURE_Q,
    FIXTURE_R,
    FIXTURES_DIR,
    HTTP_URL,
    UP_TIMEOUT_SECONDS,
    FixtureHarness,
    _files_args,
    _run,
)

TIER = "conformance"

HTTP_WAIT_SECONDS = 30
POSTGRES_WAIT_SECONDS = 60
STATE_WAIT_SECONDS = 30


def _client() -> str:
    """The real Docker Engine client: ``SHIM_CONFORMANCE_DOCKER``, read at call time.

    The ``tiers.conformance_tier`` gate already skips the test when the
    variable is unset, so a missing value here means the gate passed on a
    stale read rather than a runtime being absent: it raises instead of
    skipping.
    """
    return os.environ[tiers.CONFORMANCE_DOCKER_ENV]


def _started_at(client: str, container_name: str) -> str:
    """The ``State.StartedAt`` of one container, from ``docker inspect``."""
    result = _run(
        [client, "inspect", "--format", "{{.State.StartedAt}}", container_name]
    )
    if result.returncode != 0:
        raise AssertionError(
            f"inspect {container_name} exited {result.returncode}: "
            f"{result.stdout + result.stderr}"
        )
    return result.stdout.strip()


def _started_key(raw: str) -> str:
    """A fixed-width key so two ``State.StartedAt`` strings compare in time order.

    Docker prints RFC3339 timestamps with as few as one fractional digit
    (trailing zeros are stripped), so a raw string comparison can order two
    values from the same second wrongly: right-padding the fraction to nine
    digits makes lexicographic order equal to chronological order.
    """
    if raw.endswith("Z"):
        raw = raw[:-1]
    head, sep, fraction = raw.partition(".")
    if sep:
        return f"{head}.{fraction.ljust(9, '0')}"
    return f"{raw}.000000000"


class ConformanceFixtureTests(unittest.TestCase):
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

    def _wait_for_log(
        self,
        harness: FixtureHarness,
        service: str,
        needle: str,
        wait_seconds: float = 20.0,
    ) -> None:
        """Poll the container's logs until ``needle`` appears, or fail after the deadline.

        The live tier reads the same logs through ``compose logs <service>``;
        the conformance tier reads them raw through ``docker logs
        <container>``. Compose prefixes each line with the container name,
        which would let the short needle "hi" match the "shimtest" prefix;
        the raw stream is the line the container actually printed. A one-shot
        read right after ``up`` can miss it, hence the poll.
        """
        container = f"{harness.project}-{service}-1"
        deadline = time.monotonic() + wait_seconds
        last = ""
        while time.monotonic() < deadline:
            result = _run([_client(), "logs", container])
            if result.returncode == 0 and needle in result.stdout:
                return
            last = result.stdout + result.stderr
            time.sleep(1)
        self.fail(
            f"{container} logs never showed {needle!r} within "
            f"{wait_seconds:.0f}s:\n{last}"
        )

    def _wait_for_exited(
        self, container_name: str, wait_seconds: float = STATE_WAIT_SECONDS
    ) -> str:
        """Poll ``docker inspect`` until the container is exited; return its exit code.

        A one-shot inspect right after ``up -d`` can still show the container
        running, so the numeric exit code is read only once the state has
        settled to ``exited``.
        """
        client = _client()
        deadline = time.monotonic() + wait_seconds
        last = ""
        while time.monotonic() < deadline:
            state = _run(
                [client, "inspect", "--format", "{{.State.Status}}", container_name]
            )
            if state.returncode == 0 and state.stdout.strip() == "exited":
                code = _run(
                    [client, "inspect", "--format", "{{.State.ExitCode}}", container_name]
                )
                self.assertEqual(code.returncode, 0, code.stdout + code.stderr)
                return code.stdout.strip()
            last = state.stdout + state.stderr
            time.sleep(1)
        self.fail(f"{container_name} never reached exited within {wait_seconds:.0f}s: {last}")

    # -- tests -----------------------------------------------------------

    @tiers.conformance_tier()
    def test_fixture_a_published_port_answers_http_and_down_removes_everything(self) -> None:
        harness = FixtureHarness(self, FIXTURE_A, docker=_client(), prefix="conf")
        project = harness.project

        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        self._wait_for_http_200(HTTP_URL)

        result = harness.compose("down")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        listing = _run([_client(), "ps", "-a", "--format", "{{.Names}}"])
        self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
        self.assertNotIn(project, listing.stdout + listing.stderr)

    @tiers.conformance_tier()
    def test_fixture_b_postgres_row_survives_down_and_disappears_with_down_v(self) -> None:
        harness = FixtureHarness(self, FIXTURE_B, docker=_client(), prefix="conf")
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
        volumes = _run([_client(), "volume", "ls", "--format", "{{.Name}}"])
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
        volumes = _run([_client(), "volume", "ls", "--format", "{{.Name}}"])
        self.assertEqual(volumes.returncode, 0, volumes.stdout + volumes.stderr)
        self.assertNotIn(project, volumes.stdout + volumes.stderr)

    @tiers.conformance_tier()
    def test_fixture_o_external_volume_survives_down_v_and_is_reused(self) -> None:
        client = _client()

        # 0. Pre-create the external volume the fixture declares (its name
        #    arrives through a `.env` override) through the real client; a
        #    cleanup removes it by the exact name — `down -v` must not.
        ext = tiers.disposable_name("ext")

        def _remove_external_volume(name: str) -> None:
            # Ignore errors: the volume may already be gone.
            _run([client, "volume", "rm", name])

        # Registered before the create so a failed create is still cleaned up.
        self.addCleanup(_remove_external_volume, ext)
        result = _run([client, "volume", "create", ext])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        harness = FixtureHarness(
            self,
            FIXTURE_O,
            overrides={".env": f"EXT_VOLUME={ext}\n"},
            docker=client,
            prefix="conf",
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
        volumes = _run([client, "volume", "ls", "--format", "{{.Name}}"])
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
        volumes = _run([client, "volume", "ls", "--format", "{{.Name}}"])
        self.assertEqual(volumes.returncode, 0, volumes.stdout + volumes.stderr)
        listing = volumes.stdout + volumes.stderr
        self.assertNotIn(f"{project}_pgext", listing)
        self.assertNotIn(f"{project}_{ext}", listing)

    @tiers.conformance_tier()
    def test_fixture_p_up_service_creates_its_dependency_and_not_unrelated_services(self) -> None:
        harness = FixtureHarness(self, FIXTURE_P, docker=_client(), prefix="conf")
        project = harness.project
        client = _client()

        # 1. The up of one service pulls in its dependency closure: db and
        #    api are started, the unrelated worker never.
        result = harness.compose("up", "-d", "api", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # 2. `docker ps -a` proves the selection.
        listing = _run([client, "ps", "-a", "--format", "{{.Names}}"])
        self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
        names = listing.stdout + listing.stderr
        self.assertIn(f"{project}-db-1", names)
        self.assertIn(f"{project}-api-1", names)
        self.assertNotIn(f"{project}-worker-1", names)

        # 3. api started only after db's first passing healthcheck ended:
        #    compare api's start with the end of db's first successful probe.
        inspected = _run(
            [client, "inspect", "--format", "{{json .State.Health.Log}}", f"{project}-db-1"]
        )
        self.assertEqual(inspected.returncode, 0, inspected.stdout + inspected.stderr)
        passing = [
            entry["End"]
            for entry in json.loads(inspected.stdout)
            if entry.get("ExitCode") == 0
        ]
        self.assertTrue(passing, f"db never passed a healthcheck: {inspected.stdout}")
        first_healthy = _started_key(passing[0].replace("+00:00", "Z"))
        api_key = _started_key(_started_at(client, f"{project}-api-1"))
        self.assertLessEqual(
            first_healthy,
            api_key,
            f"api started before db was healthy: healthy={first_healthy} api={api_key}",
        )

    @tiers.conformance_tier()
    def test_fixture_p_no_deps_creates_only_the_named_service(self) -> None:
        harness = FixtureHarness(self, FIXTURE_P, docker=_client(), prefix="conf")
        project = harness.project
        client = _client()

        # 1. `--no-deps` up acts on the named service alone; real Docker
        #    prints no shim notice, so only the selection is asserted.
        result = harness.compose(
            "up", "-d", "--no-deps", "api", timeout=UP_TIMEOUT_SECONDS
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # 2. `docker ps -a` proves nothing else of the project exists.
        listing = _run([client, "ps", "-a", "--format", "{{.Names}}"])
        self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
        names = listing.stdout + listing.stderr
        self.assertIn(f"{project}-api-1", names)
        self.assertNotIn(f"{project}-db-1", names)
        self.assertNotIn(f"{project}-worker-1", names)

    @tiers.conformance_tier()
    def test_fixture_c_service_names_resolve_inside_the_api_container(self) -> None:
        harness = FixtureHarness(self, FIXTURE_C, docker=_client(), prefix="conf")

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

    @tiers.conformance_tier()
    def test_fixture_d_api_starts_only_after_db_is_healthy(self) -> None:
        harness = FixtureHarness(self, FIXTURE_D, docker=_client(), prefix="conf")
        project = harness.project
        client = _client()

        # 1. The up is issued with the same --wait-timeout the live test
        #    uses; record the wall time before the call to bound the ordering
        #    assertions below.
        before = datetime.now(timezone.utc)
        result = harness.compose("up", "-d", "--wait-timeout", "120", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # 2. Both services must be listed by `compose ps` after the up.
        listing = harness.compose("ps")
        self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
        for service in ("db", "api"):
            self.assertIn(service, listing.stdout, listing.stdout)

        # 3. Container start dates prove the ordering: db was started first,
        #    api only after, and api only once the up call was in flight.
        db_key = _started_key(_started_at(client, f"{project}-db-1"))
        api_key = _started_key(_started_at(client, f"{project}-api-1"))
        before_key = before.strftime("%Y-%m-%dT%H:%M:%S") + f".{before.microsecond * 1000:09d}"
        self.assertLessEqual(
            db_key,
            api_key,
            f"api started before db: db={db_key} api={api_key}",
        )
        self.assertGreaterEqual(
            api_key,
            before_key,
            f"api started before the up: api={api_key} before={before_key}",
        )

        # 4. The database accepts connections: the api was only started once
        #    db was healthy, so poll for readiness rather than assume the up
        #    waited for it.
        self._wait_for_postgres_ready(harness)

    @tiers.conformance_tier()
    def test_fixture_e_services_resolve_only_peers_on_a_shared_network(self) -> None:
        harness = FixtureHarness(self, FIXTURE_E, docker=_client(), prefix="conf")

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

    @tiers.conformance_tier()
    def test_fixture_e_bind_mounted_file_visible_in_container(self) -> None:
        harness = FixtureHarness(self, FIXTURE_E, docker=_client(), prefix="conf")

        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # ./conf is bind-mounted read-only at /etc/conf inside app.
        result = harness.compose("exec", "-T", "app", "cat", "/etc/conf/app.conf")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("greeting=hello", result.stdout)

    @tiers.conformance_tier()
    def test_fixture_h_dotenv_value_reaches_container(self) -> None:
        # The fixture ships `dot-env`, the intended content of a project
        # `.env` file, but the copy has no `.env`; write it via an override
        # (the header comment lines are inert to the .env parser).
        env_file = (FIXTURES_DIR / FIXTURE_H / "dot-env").read_text()
        harness = FixtureHarness(
            self, FIXTURE_H, overrides={".env": env_file}, docker=_client(), prefix="conf"
        )

        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # greeter exits right after printing $GREETING, so the .env value
        # (which must win over the ${GREETING:-hello} default) is read back
        # from the container's logs.
        self._wait_for_log(harness, "greeter", "hi")

    @tiers.conformance_tier()
    def test_fixture_h_default_value_used_without_dotenv(self) -> None:
        harness = FixtureHarness(self, FIXTURE_H, docker=_client(), prefix="conf")

        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # With no `.env` the ${GREETING:-hello} interpolation falls back to
        # the default.
        self._wait_for_log(harness, "greeter", "hello")

    @tiers.conformance_tier()
    def test_fixture_i_three_files_merge_with_override_winning(self) -> None:
        harness = FixtureHarness(self, FIXTURE_I, docker=_client(), prefix="conf")
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

    @tiers.conformance_tier()
    def test_fixture_m_read_only_volume_and_bind_reject_writes(self) -> None:
        harness = FixtureHarness(self, FIXTURE_M, docker=_client(), prefix="conf")

        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # The same markers the live tier asserts: the two short-syntax `:ro`
        # mounts and the two long-syntax `read_only: true` mounts block, the
        # read-write control accepts.
        for marker in (
            "VOL_WRITE_BLOCKED",
            "BIND_WRITE_BLOCKED",
            "VOL2_WRITE_BLOCKED",
            "BIND2_WRITE_BLOCKED",
            "RW_WRITE_OK",
        ):
            self._wait_for_log(harness, "writer", marker)

        for target in ("/vol/y", "/shared/y", "/vol2/y", "/shared2/y"):
            attempt = harness.compose("exec", "-T", "writer", "touch", target)
            self.assertNotEqual(attempt.returncode, 0, f"write to {target} succeeded")
            self.assertIn("Read-only file system", attempt.stdout + attempt.stderr)

        # The read-write control accepts the same write.
        control = harness.compose("exec", "-T", "writer", "touch", "/rw/y")
        self.assertEqual(control.returncode, 0, f"write to /rw/y failed: {control.stdout + control.stderr}")

    @tiers.conformance_tier()
    def test_fixture_n_network_mode_none_has_no_connectivity(self) -> None:
        harness = FixtureHarness(self, FIXTURE_N, docker=_client(), prefix="conf")

        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # Docker honours the isolation request: only the loopback interface.
        interfaces = harness.compose("exec", "-T", "isolated", "ls", "/sys/class/net")
        self.assertEqual(interfaces.returncode, 0, interfaces.stdout + interfaces.stderr)
        self.assertEqual(interfaces.stdout.split(), ["lo"])

    @tiers.conformance_tier()
    def test_fixture_r_selinux_refused_by_shim_but_accepted_on_engine(self) -> None:
        # The documented difference: the shim refuses `:Z` (exit 64, nothing
        # created) because Apple container cannot represent SELinux relabeling,
        # while a real Docker Engine accepts the flag — a no-op where SELinux
        # is not enabled — and starts the service.
        harness = FixtureHarness(self, FIXTURE_R, docker=_client(), prefix="conf")

        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @tiers.conformance_tier()
    def test_fixture_q_build_secrets_reach_the_build_and_never_the_output(self) -> None:
        client = _client()

        # The built image gets a disposable name; a cleanup removes it by the
        # exact name through the real client (a `down -v` never removes
        # images).
        image = tiers.disposable_name("qsec") + ":fx"
        self.addCleanup(lambda: _run([client, "image", "rm", image]))

        # The environment secret reaches the build only through the compose
        # process's environment (the harness copies os.environ at call time),
        # so the patch is entered before the harness: its cleanups then run
        # while the variable is still set.
        self.enterContext(
            mock.patch.dict(os.environ, {"Q_ENV_SECRET": "dummy-env-secret"})
        )

        harness = FixtureHarness(
            self,
            FIXTURE_Q,
            overrides={".env": f"Q_IMAGE={image}\n"},
            docker=client,
            prefix="conf",
        )

        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # The build baked `ok` into /marker and the running service prints it;
        # the secret values must appear in no output at all.
        self._wait_for_log(harness, "app", "ok")
        logs = _run([client, "logs", f"{harness.project}-app-1"])
        output = result.stdout + result.stderr + logs.stdout + logs.stderr
        self.assertNotIn("dummy-file-secret", output)
        self.assertNotIn("dummy-env-secret", output)

    @tiers.conformance_tier()
    def test_fixture_k_exited_container_reports_numeric_exit_code(self) -> None:
        harness = FixtureHarness(self, FIXTURE_K, docker=_client(), prefix="conf")
        project = harness.project

        # 1. Detached up returns 0 even though the service exits 3
        #    immediately: like Docker without --wait, a one-shot service
        #    failing is not an up failure. A failure must name the service.
        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        if result.returncode != 0:
            self.assertIn("brief", result.stderr, result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # 2. Here Docker differs from the shim: the Apple runtime exposes no
        #    numeric exit-code field, so the shim refuses --format
        #    '{{.State.ExitCode}}' with exit 64 (unit-tested in
        #    tests/test_inspect.py), while a real Docker Engine reports it.
        #    The conformance tier asserts what the real client prints: the
        #    service's 3.
        code = self._wait_for_exited(f"{project}-brief-1")
        self.assertEqual(code, "3", f"brief exited {code!r}, expected 3")

        ps = harness.compose("ps", "--format", "json", "-a")
        self.assertEqual(ps.returncode, 0, ps.stdout + ps.stderr)
        record = json.loads(ps.stdout)
        self.assertEqual(record["State"], "exited")
        self.assertEqual(record["ExitCode"], 3)

    @tiers.conformance_tier()
    def test_compose_ps_json_matches_fixture_l_service_states(self) -> None:
        harness = FixtureHarness(self, "l-partial-failure", docker=_client(), prefix="conf")
        result = harness.compose("up", "-d", timeout=UP_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        code = self._wait_for_exited(f"{harness.project}-flaky-1")
        self.assertEqual(code, "1", f"flaky exited {code!r}, expected 1")
        ps = harness.compose("ps", "--format", "json", "-a")
        self.assertEqual(ps.returncode, 0, ps.stdout + ps.stderr)
        records = [json.loads(line) for line in ps.stdout.splitlines()]
        by_service = {record["Service"]: record for record in records}
        self.assertEqual(by_service["web"]["State"], "running")
        self.assertEqual(by_service["flaky"]["State"], "exited")
        self.assertEqual(by_service["flaky"]["ExitCode"], 1)


if __name__ == "__main__":
    unittest.main()
