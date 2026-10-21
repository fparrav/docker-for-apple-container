# Tasks

New logic lives in two new modules so that workers edit small files:
`src/container_docker_shim/compose_health.py` (healthcheck model, check runner,
wait loop) and `src/container_docker_shim/compose_reconcile.py` (configuration
hash and per-service reconciliation decision). `compose.py` only wires them in.
Python 3.9 compatible, standard library only, `from __future__ import annotations`.

## 1. Spikes against the live 1.5.0 runtime

- [x] 1.1 With a disposable named container that exits with code 3, record in `findings.md` how `container inspect --format json` reports the stopped state and exit code, and verify the container is removed afterwards @worker:orchestrator @review:none
- [x] 1.2 Decide from 1.1 whether `service_completed_successfully` can be enforced; if no exit-code field exists, record that and mark task 3.4a as "refuse with exit 64" @worker:orchestrator @review:none
- [x] 1.3 Measure on this machine the time of `container exec <name> true` against a running container (ten runs) and record the median and maximum in `findings.md`, to size the default poll interval @worker:orchestrator @review:none
- [x] 1.4 (was 4.1) Spike: on a throwaway user network, check whether two containers resolve each other by name without an admin DNS domain, and whether `--dns-domain` changes that; record the evidence in `findings.md` and the adopt-or-keep decision @worker:orchestrator @review:none

## 2. Compose model additions

- [x] 2.1a Create `src/container_docker_shim/compose_health.py` with a frozen dataclass `Healthcheck(test, interval, timeout, retries, start_period, start_interval, disabled)` (seconds as floats), `parse_duration(value) -> float` (accepts `30s`, `1m30s`, `500ms`, `2h`, a bare number meaning seconds, and rejects anything else with `ValueError`) and `parse_healthcheck(spec) -> Healthcheck | None` for the compose `healthcheck` mapping: defaults interval 30, timeout 30, retries 3, start_period 0, start_interval 5; `test: ["NONE"]` or `disable: true` gives `disabled=True`; a string `test` means `["CMD-SHELL", <string>]`; `None` for a service without the key @worker:local
- [x] 2.1b Create `tests/test_compose_health.py` with unit tests for `parse_duration` (each unit, combination, invalid) and `parse_healthcheck` (defaults, every field, string test, list test, `["NONE"]`, `disable: true`, missing key); verify `python3 -m unittest tests.test_compose_health` passes @worker:local
- [x] 2.2a In `compose.py` extend `Service` with `depends_conditions: dict[str, str]` (dependency name to condition, default `service_started`) filled from `depends_on` list and map forms without changing `Service.depends_on`; raise `ShimError(..., 64)` naming the service, the dependency and the condition when the condition is not one of `service_started`, `service_healthy`, `service_completed_successfully` @worker:local
- [x] 2.2b In `tests/test_compose.py` add tests for list form, map form with each of the three conditions, and an unknown condition exiting 64 through `Project` construction; verify `python3 -m unittest tests.test_compose` passes @worker:local
- [x] 2.3a In `compose.py` add `_validate_dependencies(services)` called from `cmd_up` right after `_enforce_run_security(services)`: for every dependency with condition `service_healthy` whose target service has no enabled healthcheck (`parse_healthcheck` returns `None` or `disabled`), raise `ShimError(message, 64)` naming both services; verify `python3 -m unittest tests.test_compose` passes @worker:local
- [x] 2.3b In `tests/test_compose.py` add an end-to-end test that `up` with that configuration exits 64 and the fake runtime state has no container and no `run` action; verify it passes @worker:local

## 3. Health waiting and `--wait`

- [x] 3.1a In `compose_health.py` add `run_check_once(exec_fn, container, check) -> tuple[bool, str]` where `exec_fn(argv: list[str])` returns an object with `returncode`, `stdout` and `stderr`; it builds `container exec <container> <argv>` content from `check.test` (`CMD` runs the remaining items directly, `CMD-SHELL` runs `sh -c <string>`), returns `(returncode == 0, combined output trimmed to 2000 characters)` and treats a raised `OSError` as a failed check @worker:local
- [x] 3.1b In `tests/test_compose_health.py` add tests of `run_check_once` with a fake `exec_fn` for `CMD`, `CMD-SHELL`, a non-zero exit and an `OSError`, asserting the exact argv given to `exec_fn`; verify they pass @worker:local
- [x] 3.2a In `compose_health.py` add `wait_healthy(check_once, check, state, clock, sleep, timeout=None) -> WaitResult` (dataclass with `status` in `healthy`, `unhealthy`, `exited`, `timeout`, plus `attempts` and `last_output`). `state()` returns `"running"` or `"exited"`; `clock()` returns seconds; `sleep(seconds)`. Algorithm: poll every `start_interval` while `clock() - start < start_period`, otherwise every `interval`; a pass at any time returns `healthy`; a failure during `start_period` does not count; a failure after it counts, `retries` consecutive counted failures return `unhealthy`; `state() == "exited"` returns `exited` immediately; when `timeout` (default `max(start_period + retries * (interval + timeout), interval)` of the check, the floor keeps `retries: 0` from timing out before a first attempt) elapses return `timeout`; no real sleeping or clock access inside the function @worker:local
- [x] 3.2b In `tests/test_compose_health.py` add tests of `wait_healthy` with a fake clock and sleep that advance virtual time: success on the first check, success inside `start_period` after failures, retry exhaustion, container exited early, overall timeout, and that a pass after earlier failures returns `healthy` at once (a pass ends the wait, so no counter reset is observable) unless the retries ran out first; verify they pass without real sleeping (the module runs in under one second) @worker:local
- [x] 3.3 Wire `service_healthy` into `cmd_up`: after creating a service that some dependent needs `service_healthy` from, call `wait_healthy` with `_run_container_capture(["exec", ...])` and an exited-state probe via `container inspect`; create dependents only after `healthy`; on any other result print the service, the last check output and the last 40 log lines, and exit non-zero without removing running services; add `--wait-timeout` seconds as the `timeout` override @worker:orchestrator
- [x] 3.3b In `tests/test_compose.py` add end-to-end tests of `up -d` with `service_healthy` using `self.script_exec(container, codes)` (the fake `exec` returns the scripted codes in order, the last repeating): (1) the dependency is healthy after two failures, assert the `web` container is created only after the third `exec` of `demo-db-1` in `action_log`; (2) the dependency never passes with `retries: 2`, assert exit 1, stderr names the service and `last log lines`, `demo-web-1` is never created and `demo-db-1` stays in the state; (3) `--wait-timeout 1` with a never-passing check and a long interval exits 1 with "did not become healthy"; use healthcheck intervals of `0.05` seconds so the tests stay fast; verify they pass @worker:local
- [x] 3.4a Wire `service_completed_successfully` into `cmd_up` per finding 1.2 (finding 1.2: run the dependency in the foreground with `container run` (without `-d`), which returns the container exit code like `container start -a` does, and use that return code: 0 continues, non-zero fails `up` with the service name and the last 40 log lines) @worker:orchestrator
- [x] 3.4b In `tests/test_compose.py` add tests for the three outcomes of 3.4a using the fake runtime; verify they pass @worker:local
- [x] 3.5a Add `--wait` and `--wait-timeout <seconds>` to `docker compose up` in `compose.py` (`_dispatch` and `cmd_up`): `--wait` implies detached, waits until every started service is running and, when it has an enabled healthcheck, healthy; exits non-zero on timeout or early exit @worker:orchestrator
- [x] 3.5b In `tests/test_compose.py` add tests of `up --wait` for success, timeout and a service that exits early; verify they pass @worker:local
- [x] 3.6a Remove `healthcheck` from `UNSUPPORTED_SERVICE_KEYS` and drop the `depends_on` condition warning in `_warn_unsupported` (`compose.py`) @worker:orchestrator
- [x] 3.6b Update `test_deferred_compose_features_warn_and_keep_one_service_container` in `tests/test_compose.py` (the `depends_on conditions ignored` assertion is obsolete), and add tests that a declared healthcheck nobody gates on warns once per service that it is "only evaluated to gate depends_on service_healthy or 'up --wait'", that the warning is absent with `up --wait` and for a gated service, and that an invalid healthcheck (for example `interval: abc`) makes `up` exit 64 before any container is created; verify `python3 -m unittest tests.test_compose` passes @worker:local

## 4. Service discovery

- [x] 4.2 Add to `tests/test_compose.py` a fixture project with an `api` service depending on a `db` service, and an end-to-end test asserting the commands the fake runtime records to put `db` into the api container's hosts file with the db address, including a second run after the db address changed; verify it passes @worker:local
- [x] 4.3a Make the hosts update in `_inject_etc_hosts` (`compose.py`) idempotent under repetition and address change (a second run produces no duplicate line and replaces a stale address); the existing strip-and-append logic already was, so it is extracted into `_hosts_rewrite_script` to make it testable @worker:orchestrator
- [x] 4.3b Add tests for 4.3a in `tests/test_compose.py`: `compose._hosts_rewrite_script(lines, managed, path)` (the idempotent script extracted from `_inject_etc_hosts`) is run through `sh -c` against a temporary hosts file; assert that running it twice leaves exactly one line per managed name and that running it with a changed address replaces the stale address, while unrelated lines (`127.0.0.1 localhost`) survive; verify it passes @worker:local
- [x] 4.4 Verify the refresh after `docker restart` and `docker compose restart` of the database, covering the shell-less image path with the copy-based fallback; fix `compose.py` where a test shows a gap and record the result in `findings.md` @worker:orchestrator

## 5. Idempotent `up`

- [x] 5.1a Create `src/container_docker_shim/compose_reconcile.py` with `config_hash(spec, image_id) -> str` (SHA-256 hex over canonical JSON with sorted keys of the normalized spec dictionary after removing `depends_on`, `build` and keys starting `x-`, plus the image ID) @worker:local
- [x] 5.1b Create `tests/test_compose_reconcile.py` testing that key order and string whitespace in YAML text parsed by `compose.parse_yaml` do not change the hash, that an environment change, an image ID change and an image tag change do change it; verify it passes @worker:local
- [x] 5.2a In `compose.py` write the labels `com.docker.compose.config-hash` and `com.docker.compose.image` on created containers (hash from 5.1a, image ID read with `container image inspect` through the existing image helpers) @worker:orchestrator
- [x] 5.2b In `tests/test_compose.py` add a test that the labels appear in the fake runtime state after `up`; verify it passes @worker:local
- [x] 5.3a In `compose_reconcile.py` add `decide(existing, wanted_hash, wanted_image, force, no_recreate) -> str` returning `keep`, `start`, `recreate` or `create` (container missing: `create`; labels missing or different: `recreate`; same and stopped: `start`; same and running: `keep`; `force` always `recreate` for an existing container; `no_recreate` turns `recreate` into `keep` or `start`) @worker:local
- [x] 5.3b Add tests of `decide` to `tests/test_compose_reconcile.py` for every row above; verify they pass @worker:local
- [x] 5.3c Replace the unconditional removal in `cmd_up` (`compose.py`) with per-service reconciliation using `decide`, recreating dependents of a recreated service and printing what is recreated and why @worker:orchestrator
- [x] 5.3d In `tests/test_compose.py` add end-to-end tests for unchanged (no container touched), stopped (started, not recreated), changed (recreated) and missing-label (recreated) cases; verify they pass @worker:local
- [x] 5.4a Add `--force-recreate`, `--no-recreate` and `--remove-orphans` to `up` in `compose.py`, warn on orphan containers by default and remove them only with `--remove-orphans` @worker:orchestrator
- [x] 5.4b Add one test per flag to `tests/test_compose.py`; verify they pass @worker:local
- [x] 5.5a Make a failed `up` leave running services untouched and report the failing service in `compose.py` @worker:orchestrator
- [x] 5.5b Add a test that a failing service keeps the earlier services running and that a second `up` after fixing the file starts only the missing service; verify it passes @worker:local

## 6. Volume safety

- [x] 6.1 Add tests to `tests/test_compose.py` that `down` removes no volume, `down -v` removes only project-labelled named volumes, and external or other-project volumes survive; verify they pass @worker:local
- [x] 6.2 Add a test to `tests/test_compose.py` that recreating a service keeps its named volumes and issues no volume removal action; verify it passes @worker:local

## 7. Live verification (opt-in tier)

- [x] 7.1 Create `tests/test_live_compose.py` (skipped unless `SHIM_TIERS` contains `live`, `container system status` reports 1.5.0 and the images `postgres:17` and `alpine:3.20` exist locally; project name prefix `shimtest-cmp-`; cleanup through `docker compose down -v` of that project only) with a test of an API + PostgreSQL project: the `db` service has a `pg_isready` healthcheck, the `api` service (the postgres image used as client, command `sleep infinity`) depends on `service_healthy`; connect from api with `psql -h db`, write a row, run `down`, run `up -d` again, read the row, then `down -v` and verify cleanup with `container ls -a` and `container volume ls` @worker:local
- [x] 7.2 In the same file add a live test that restarts the database with `docker compose restart db` and verifies `psql -h db` still works from the api container @worker:local
- [x] 7.3 In the same file add a live test of a dependent gated on a dependency that becomes healthy after a delay (healthcheck file created by the service after 5 s) and one of a dependency that never becomes healthy and hits `--wait-timeout` with a non-zero exit @worker:local
- [x] 7.4 Run the live tests on this machine with `SHIM_TIERS=live`, record real results in `findings.md` (a test not run is reported as not run), and verify no `shimtest-cmp-` container or volume remains @worker:orchestrator @review:none

## 8. Documentation and validation

- [x] 8.1 Update the README Compose section (conditions, `--wait`, idempotent `up`, orphans, volume rules, discovery behaviour) and remove the statements that health-gated dependencies and recreation-on-every-up are the current behaviour @worker:orchestrator @review:none
- [x] 8.2 Run `python3 -m unittest discover -s tests -v` and verify it passes @worker:orchestrator @review:none
- [x] 8.3 Run `openspec validate compose-lifecycle --strict` and verify it passes @worker:orchestrator @review:none
