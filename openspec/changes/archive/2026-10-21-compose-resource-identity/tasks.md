# Tasks

Conventions for every task:

- Python 3.9 standard library only, `from __future__ import annotations`, unittest only. Tests are top-level modules in `tests/` with a module-level `TIER`; live tests are opt-in (`SHIM_TIERS=live`), name objects `shimtest-<topic>-<short uuid>`, clean up by exact name, never prune.
- Each task lists `Priority` (P0 must ship, P1 should ship), `Files`, `Depends` and `Done`. Tasks in the same group touch different files unless stated.
- Matrix entries (`tests/compat/matrix.json`) land in the task that adds the behaviour; the README matrix block is regenerated with `python3 scripts/render_compat_table.py --write README.md`.
- Do not stage `AGENTS.md`. PostgreSQL fixtures set `PGDATA` to a subdirectory of the mount (named volumes contain `lost+found`).

## 1. Pure modules (no behaviour change yet)

- [x] 1.1 Add `src/container_docker_shim/compose_resources.py` with `Resource` and `resolve(project)` implementing the name order of design D1 for networks and volumes, plus `removable(resources, labelled)` implementing D3 @worker:local
  - Priority: P0. Files: `compose_resources.py` (new), `tests/test_compose_resources.py` (new, `TIER = "unit"`). Depends: none.
  - Tests: managed, explicit `name`, external with and without `name`, `default` network, official-model input and built-in-parser input giving the same names; `removable` excludes external, other-project and unlabelled resources.
  - Done: `python3 -m unittest tests.test_compose_resources -v` passes with no runtime call.
- [x] 1.2 Add `src/container_docker_shim/compose_plan.py` with `plan(project, selected, no_deps)` implementing D5, including notices, optional dependencies, cycle, undefined and inactive-profile errors @worker:orchestrator
  - Priority: P0. Files: `compose_plan.py` (new), `tests/test_compose_plan.py` (new, `TIER = "unit"`). Depends: 1.3.
  - Tests: direct, transitive, diamond, independent service excluded, `--no-deps`, optional present/absent, cycle, undefined dependency, unknown selected name.
  - Done: `python3 -m unittest tests.test_compose_plan -v` passes.
- [x] 1.3 Parse `depends_on.required` into `Service.depends_required` in `_parse_depends_conditions` (default `True`) and keep `depends_conditions` unchanged @worker:orchestrator
  - Priority: P1. Files: `compose.py` (`Service`, `_parse_depends_conditions`), `tests/test_compose.py`. Depends: none.
  - Done: tests for list form, map form, `required: false` and the default pass; existing dependency tests unchanged.

## 2. Resource identity

- [x] 2.1 Use `Resources` for every name: `_named_volume_args`, `_service_networks`, `_ensure_network`, `_ensure_named_volume`, `_select_shared_attachment`, `_inject_etc_hosts` read runtime names from the resolved set; delete `_NETWORK_SPECS`; write the resource-key label on creation @worker:orchestrator
  - Priority: P0. Files: `compose.py`, `tests/test_compose.py`. Depends: 1.1.
  - Tests: managed names unchanged (`demo_data`); external volume mounted as `shared_data` and `volume create` never called for it; explicit `name`; external network attached by its own name; discovery lines still written for peers on an external network.
  - Done: `python3 -m unittest tests.test_compose -v` passes after the next task replaces the old external-volume cases.
- [x] 2.2 Replace `_write_demo_compose`, `_seed_demo_volumes`, `test_down_without_v_keeps_all_volumes` and `test_down_v_removes_only_project_labeled_volumes` so they assert the real external volume is mounted and survives, no `demo_shared` exists, and another project's volume survives @worker:orchestrator
  - Priority: P0. Files: `tests/test_compose.py`. Depends: 2.1.
  - Done: the new tests fail on the previous implementation (check once by stashing 2.1) and pass now.
- [x] 2.3 Add the external existence pass and the other-project / unlabelled checks (design D2) before `_ensure_network`, with exit 1 and Docker-style messages @worker:orchestrator
  - Priority: P0. Files: `compose.py`, `tests/test_compose.py`. Depends: 2.1.
  - Tests: missing external volume and network create nothing (assert no `create` call on the fake runtime); other-project label refuses; unlabelled reuse prints a notice; a runtime error on `inspect` aborts before creation.
  - Done: new tests pass.
- [x] 2.4 Rewrite `cmd_down` to remove only `removable(...)`, refuse `-v` without a Compose file, and print the legacy-copy notice in `cmd_up` (D4) @worker:orchestrator
  - Priority: P0. Files: `compose.py`, `tests/test_compose.py`. Depends: 2.1.
  - Tests: declared-external-now protects an existing labelled volume; `down -v` without a file exits 64 and removes nothing (containers included); plain `down` without a file still works from labels; the legacy notice appears once and the copy is not touched.
  - Done: new tests pass and the matrix gets entries for `compose volumes external`, `compose volumes name`, `compose networks external`, `compose networks name` with fake evidence; README block regenerated.

## 3. Service selection

- [x] 3.1 Make `cmd_up` consume `plan(...)`; parse `--no-deps`; print notices; keep `_enforce_run_security` and `_validate_dependencies` receiving the planned set @worker:orchestrator
  - Priority: P0. Files: `compose.py` (`cmd_up`, the `up` branch of the dispatcher), `tests/test_compose.py`. Depends: 1.2, 1.3.
  - Tests: with the fake runtime `up -d api` records `run` for `db` then `api` and none for `worker`; `service_healthy` on `db` is evaluated; `--no-deps` records only `api`; an unknown name exits 1 with no create call; a stopped dependency is started.
  - Done: new tests pass; existing `up` tests unchanged.
- [x] 3.2 Apply `required: false` in the wait path: a failed or missing wait for an optional dependency becomes a notice, and `service_healthy` on an optional dependency without a healthcheck is not refused @worker:orchestrator
  - Priority: P1. Files: `compose.py` (`_validate_dependencies`, `_wait_service_healthy` caller), `tests/test_compose.py`, `tests/test_compose_health.py` if needed. Depends: 3.1.
  - Done: tests for optional unhealthy and optional without healthcheck pass; a required one still fails.
- [x] 3.3 Matrix entries and docs: `compose up --no-deps`, `compose depends_on required`, README Compose section ("Named volumes", "Teardown", `up` selection, migration note for external volumes) @worker:orchestrator @review:none
  - Priority: P1. Files: `tests/compat/matrix.json`, `README.md`, `tests/test_docs.py` if a rule needs updating. Depends: 2.4, 3.2.
  - Done: `python3 scripts/render_compat_table.py --check README.md` exits 0 and `python3 -m unittest tests.test_docs tests.test_compat_matrix -v` passes.

## 4. Live and conformance evidence

- [x] 4.1 Add fixture `tests/fixtures/compat/o-external-volume/` (PostgreSQL, `PGDATA` below the mount, volume `external: true` with `name: ${EXT_VOLUME}`) and live tests: pre-create a `shimtest-` volume, `up`, write a row, `down -v`, assert volume and row remain, `up` again and read the row; assert no `<project>_` copy exists; conformance test runs the same file on Docker @worker:local
  - Priority: P0. Files: `tests/fixtures/compat/o-external-volume/`, `tests/test_live_fixtures.py`, `tests/test_conformance_fixtures.py`. Depends: 2.4.
  - Done: with `SHIM_TIERS=live` the test passes and removes every `shimtest-` object; the default run reports it skipped with a reason; matrix entries for the external cases move to evidence `live`.
- [x] 4.2 Add fixture `tests/fixtures/compat/p-selective-up/` (`api` depends on `db` healthy, plus an unrelated `worker`) and live tests: `up -d api` creates `db` and `api` only, `api` starts after `db` is healthy, `--no-deps` creates `api` alone @worker:local
  - Priority: P0. Files: `tests/fixtures/compat/p-selective-up/`, `tests/test_live_fixtures.py`, `tests/test_conformance_fixtures.py`. Depends: 3.1.
  - Done: live test passes and cleans up; conformance variant is written and, when no Docker client is configured, reports "not run".
- [x] 4.3 Integration: run the default suite, `python3 scripts/validate-runtime.py` and the live tier for fixtures A to L, O and P; record the results and the tiers that did not run in `findings.md` of this change @worker:orchestrator @review:none
  - Priority: P0. Files: `openspec/changes/compose-resource-identity/findings.md` (new). Depends: all above.
  - Done: `findings.md` lists commands, counts, skipped tiers by name and unresolved failures.

## Workflow follow-up

- Archive after review; the deltas modify `compose-volume-safety` and `compose-dependency-conditions` and add two capabilities.
- Coordinate order with `compose-capability-gate`: the gate validates the planned set once both are merged.
