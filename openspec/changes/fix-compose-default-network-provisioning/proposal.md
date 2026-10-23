## Why

`docker compose up` always creates, checks and prints the implicit `<project>_default` network, even when every selected service attaches only to explicit networks. A project whose services use just an external network (`edge`) therefore gets an extra managed network it never uses, and an unused `default` that belongs to another project, or an unused declared external `default` that does not exist, makes `up` fail. Docker Compose creates the implicit network only when a service uses it.

The fake-runtime test that should prove this (`test_external_network_is_attached_by_its_own_name_and_peers_resolve`) cannot catch it: it asserts on `action_log`, which the fake never feeds with `network create`, so the assertion passes whatever the shim does.

Observed in the code at `bd19186` (still current on `main`):

- `cmd_up` calls `_ensure_network(project, "default")` and seeds `referenced` with `"default"` unconditionally (`compose.py`, around line 2595).
- `_check_resources` adds `default` to the networks it verifies with `setdefault("default", ...)` (around line 1907).
- `_print_dry_run` prepends `"default"` to the networks it prints (around line 2531).
- `compose_resources.resolve` always adds `default` to the resolved networks, which is correct for naming, but `down` and the three call sites above treat the presence of the key as use.

## What Changes

- Compute the set of networks an `up` needs once, from the services the plan selects, and use that set for the read-only check, the dry run and the creation.
- Create, check and print `<project>_default` only when a selected service attaches to `default`, either implicitly (no `networks` key after normalization) or by naming it.
- A declared top-level `default` that no selected service uses is neither created nor checked.
- Make the fake-tier assertions about network creation read the runtime call log, so a stray `network create` fails a test; keep `action_log` unchanged.
- Add tests for the ten network scenarios in the audit, including the final state of the fake runtime, not only the calls.
- Strengthen the existing live fixture `e-networks-volumes` (no service uses `default`) with an assertion that `<project>_default` does not exist; the declared live test count stays at 40.

**BREAKING (minor)**: a project whose services all use explicit networks no longer gets `<project>_default`. Anything that relied on that unused network existing must declare and use it.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `compose-resource-identity`: the implicit `default` network is provisioned only when a selected service uses it.
- `compose-dry-run`: the dry run lists the same networks `up` would create and check.
- `compose-service-selection`: the networks follow the selected services, with or without their dependencies.
- `test-tiers`: fake-tier tests prove that no resource was created by reading the runtime call log.

## Impact

- `src/container_docker_shim/compose_plan.py` (new pure function and a `networks` field on the plan) and `src/container_docker_shim/compose.py` (`cmd_up`, `_check_resources`, `_print_dry_run`, `_service_network_keys`).
- `tests/test_compose.py`, `tests/test_compose_plan.py`, `tests/test_live_fixtures.py`.
- No change to `down`: it already removes only resources that carry the project label, so a project that never created `default` tears down cleanly and one created by an earlier version is still removed.
- No change to `network_mode` refusals, external-network identity resolution, `/etc/hosts` discovery, volumes, `depends_on`, reconciliation, build secrets or the capability gate.
- Live evidence: this change edits `src/` and `tests/`, so the recorded tree hash stops matching and a final tag needs new evidence (see design D7). Pre-releases are unaffected.

## Out of scope

- Any redesign of the shim, Docker Engine behaviour, the Docker API or socket, `down -v` on orphaned volumes, restart or scaling policies, automatic releases and new dependencies.
- Changing how a `default` that is used is named, labelled or removed.
