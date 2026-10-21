# Findings (Apple `container` 1.5.0, macOS 26, arm64)

## 1.1 Stopped-container state and exit code

`container inspect shimtest-cmp-spike1` for a container whose command is
`sh -c 'exit 3'` reports `status.state == "stopped"` and `status.startedDate`.
There is no exit-code field in `inspect` or in `ls -a --format json`
(searched for `exit` and `code`). The container was removed afterwards.

The exit code is observable only through the foreground commands:

- `container start -a <name>` on a created container returns the container's exit
  code as its own return code (3 in the experiment, again 3 on a second start).
- `container run <image> sh -c 'exit 5'` (no `-d`) returns 5.

There is no `container wait`.

## 1.2 `service_completed_successfully`

Enforceable. The shim runs the dependency in the foreground (`container run`
without `-d`, whose return code is the container exit code, same as
`container start -a`) and uses that return code: 0 continues, non-zero fails `up` with the service name
and the tail of its logs. It blocks until the dependency exits, which is what the
condition means. No refusal is needed, so task 3.4a implements it, not the
"refuse with exit 64" branch.

## 1.3 `container exec <name> true` latency

Ten runs against a running alpine container: median 0.065 s, maximum 0.088 s
(range 0.051 to 0.088 s). A healthcheck probe is cheap, so the compose
`interval` and `start_interval` values are used unchanged with no extra floor.

## 1.4 Name resolution on a user network

On `container network create` networks, two containers do not resolve each other
by name. `nslookup` against the default resolver (`nameserver 192.168.65.1`)
answers `NXDOMAIN`; `getent hosts` and `ping` fail. Running the target with
`--dns-domain test` adds `domain test` to its `resolv.conf` but does not make
`name` or `name.test` resolve from the other container. Registering an admin DNS
domain needs privileges and is not used.

Decision: keep the shim's `/etc/hosts` injection for service discovery and make
it idempotent (tasks 4.3a and 4.3b). Native resolution is not adopted.

## 4.4 Refresh after restart (live)

Verified on this machine with a disposable `shimtest-cmp-` project (postgres:17 db with
a `pg_isready` healthcheck plus a dependent api): after `docker restart <project>-db-1`,
`docker compose exec api psql -h db` succeeded on the first attempt, so the
`/etc/hosts` entry of the dependent is refreshed. `docker compose restart db` is covered
live by `test_restart_db_keeps_api_connectivity`. The shell-less image fallback is
covered only by the fake-runtime tests; no live check was run for it (no shell-less
image is available locally).

## 7.4 Live results (SHIM_TIERS=live, container 1.5.0)

`python3 -m unittest tests.test_live_compose -v`: 4 tests, all OK in 68.5 s.

- `test_api_reaches_postgres_and_data_survives_down_and_up`: OK (also OK on its own, 21.5 s).
- `test_restart_db_keeps_api_connectivity`: OK.
- `test_dependent_waits_for_delayed_healthy_dependency`: OK.
- `test_never_healthy_dependency_hits_wait_timeout`: OK.

After the runs `container ls -a` and `container volume ls` list no `shimtest-cmp-`
resource. Codex review of the file asked to drop the `elapsed >= 4.0` check; rejected:
it is a lower bound only (the healthcheck cannot pass before the 5 s sleep ends), so
it cannot make the test flaky and it proves the dependent actually waited.
