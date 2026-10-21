# Proposal

## Why

The Compose support orders services but does not guarantee that a dependency is
usable, and it is destructive on repeat runs:

- `depends_on` conditions other than `service_started` are warned about and
  ignored, and `healthcheck` is ignored. An API starts as soon as PostgreSQL's
  container exists, not when it accepts connections.
- `docker compose up` removes every existing container of the project and
  recreates it (`cmd_up`), even when nothing changed. Running `up -d` twice
  restarts the database.
- `up --wait` does not exist.
- Service-name resolution rests on `/etc/hosts` edits whose behaviour after
  restarts, address changes and shell-less images is documented but not covered
  by a test that proves an API reaches PostgreSQL by the name `db`.
- `down` and volume handling rely on labels, but nothing proves that
  `down` keeps named volumes and that `down -v` never touches external ones.

The target workflow is a conventional API + PostgreSQL (+ Redis) project that
works unchanged from a teammate's Compose file.

## What Changes

- Implement `depends_on` conditions `service_started`, `service_healthy` and
  `service_completed_successfully`. Healthchecks run as `container exec`
  commands during `up`, honouring `test`, `interval`, `timeout`, `retries`,
  `start_period` and `start_interval`, with a bounded total wait and a
  `--wait-timeout` option. No resident monitor is introduced.
- Add `docker compose up --wait` and `--wait-timeout`.
- Make `up` convergent: store a configuration hash and the image ID as labels;
  keep containers whose hash is unchanged (starting them if stopped), recreate
  only changed services and their dependents, and support `--force-recreate`
  and `--no-recreate`.
- Warn about orphan containers instead of removing them.
- Define and test service discovery by name: an API reaches PostgreSQL by `db`
  on first start and after `docker compose restart` and `docker restart`.
  Evaluate native name resolution available in 1.5.0 before keeping the
  `/etc/hosts` approach as is.
- Guarantee volume safety: `down` removes no volume, `down -v` removes only
  project-labelled named volumes, external volumes are never removed.
- Define partial-failure behaviour: a failed `up` reports the failing service,
  leaves a state that a second `up` converges from, and never removes data.

## Capabilities

### New Capabilities
- `compose-dependency-conditions`: healthchecks, `depends_on` conditions and
  `up --wait`.
- `compose-idempotent-up`: change detection and selective recreation.
- `compose-service-discovery`: name resolution between services.
- `compose-volume-safety`: persistence and removal rules for volumes.

### Modified Capabilities

None.

## Impact

- Code: `src/container_docker_shim/compose.py` (`cmd_up`, `cmd_down`,
  `_build_run_args`, a new health-wait helper, label additions),
  `cli.py` only if shared helpers are needed.
- Labels: new `com.docker.compose.config-hash` (Docker's own name) and
  `com.docker.compose.image` labels on containers. Existing containers
  without them are recreated once.
- Tests: `tests/test_compose.py` plus fixtures for API + PostgreSQL, API +
  PostgreSQL + Redis, and a health-gated app; live tests in the opt-in tier.
- Docs: README Compose section.
- Not changed: the shim keeps no state of its own; everything lives in labels.
- Out of scope: restart policies, scaling, `compose run`, a persistent health
  monitor, and `State.Health` in `docker inspect` (health is evaluated during
  `up`, not continuously).
