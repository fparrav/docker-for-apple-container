# Tasks

Conventions for every task:

- Python 3.9 standard library only, `from __future__ import annotations`, unittest only. Tests are top-level modules in `tests/` with a module-level `TIER`; live tests are opt-in (`SHIM_TIERS=live`), name objects `shimtest-<topic>-<short uuid>`, clean up by exact name, never prune. Run `python3 -m unittest discover -s tests -v` before closing a group.
- Each task lists `Priority` (P0 must ship, P1 should ship), `Files`, `Depends` and `Done`. Tasks in the same group touch different files unless stated. `compose.py` is a hotspot: every task that edits it is chained through `Depends`, so no two run at once.
- Matrix entries (`tests/compat/matrix.json`) land in group 5; the README block is regenerated with `python3 scripts/render_compat_table.py --write README.md`. A matrix row may claim `live` or `conformance` only when a test in this change proves it.
- The supported runtime stays `container 1.5.0`; do not touch `SUPPORTED_CONTAINER_VERSION`. Do not stage `openspec/` or `AGENTS.md`. No secret value, even a dummy one, may be printed by a test on failure.
- Reproduction projects for the three gaps are in `findings.md`; recreate them as fixtures, do not point tests at a scratch path.

## 1. Pure foundations (no behaviour change yet)

- [x] 1.1 Add the mount policy to `mounts.py`: `MODE_RULES`, `ATTRIBUTE_RULES`, `check_short_modes(mode, target)`, `mount_from_entry(entry, project_dir, volume_name)` as in design D4, with `parse_volume_spec` reading the table (behaviour of `docker run -v` unchanged) @worker:local
  - Priority: P0. Files: `src/container_docker_shim/mounts.py`, `tests/test_mounts.py` (`TIER = "unit"`). Depends: none.
  - Tests: parametrized over bind and named sources x short and long syntax: `ro`, `rw`, `cached`, `delegated`, `z`, `Z`, `ro,Z`, `Z,ro`, `bind.selinux` `z`/`Z`, empty `selinux`, `nocopy`, `propagation`, `subpath`, `consistency`, unknown mode, unknown nested key, comma in source, relative target; every refusal names the option and the target.
  - Done: `python3 -m unittest tests.test_mounts tests.test_cli -v` passes; existing `docker run` volume and mount tests are unchanged.
- [x] 1.2 Add `src/container_docker_shim/compose_build_secrets.py` with `BuildSecret`, `resolve`, `check`, `to_args` as in design D2 @worker:local
  - Priority: P0. Files: `compose_build_secrets.py` (new), `tests/test_compose_build_secrets.py` (new, `TIER = "unit"`). Depends: none.
  - Tests: string and mapping entries; `target` becomes the id; file path absolute and relative (resolved against the project directory); `$` in a path; `environment` kind; undefined, `external`, both and neither source, unknown attribute, `uid`/`gid`/`mode` notice; `check` with an injected `isfile` and `environ` (missing file, unset variable, set-but-empty variable accepted); a sentinel secret value placed in `environ` never appears in any returned string.
  - Done: `python3 -m unittest tests.test_compose_build_secrets -v` passes with no filesystem or runtime access.
- [x] 1.3 Make `convert_model` keep build-referenced secrets and un-escape every preserved section; add `DROPPED_TOP_LEVEL` (design D1) @worker:local
  - Priority: P0. Files: `src/container_docker_shim/compose_normalizer.py`, `tests/test_compose_normalizer.py`, new normalized fixtures under `tests/fixtures/compose/normalized/` recorded with `docker-compose` 5.6.0 (`build-secrets.json`, `mounts-selinux.json`). Depends: none.
  - Tests: referenced file and environment secrets kept with absolute path; unreferenced and runtime-only secrets absent; non-mapping `secrets` raises `NormalizerError` naming `Docker Compose <version>`; `$$` becomes `$` in secret paths and in `volumes`/`networks` labels; service handling unchanged.
  - Done: `python3 -m unittest tests.test_compose_normalizer -v` passes and the two new fixtures load in the existing fixture tests.
- [x] 1.4 Contract test CT1: every top-level key of every `tests/fixtures/compose/normalized/*.json` is in the model or in `DROPPED_TOP_LEVEL`, and the model's `secrets` equals the set referenced by builds @worker:local
  - Priority: P0. Files: `tests/test_contract_normalizer_loss.py` (new, `TIER = "unit"`). Depends: 1.3.
  - Done: the test passes, and adding an unlisted key to a copy of a fixture makes it fail (assert this inside the test with a mutated copy).

## 2. Repeated `--env-file` (P2)

- [x] 2.1 Parse global options in `main()`: `--env-file` repeatable into an ordered list, the `=` spellings of `--env-file`, `--project-directory`, `--file`, `--profile`, `--project-name`, and exit 64 for a missing or empty value @worker:orchestrator
  - Priority: P0. Files: `src/container_docker_shim/compose.py` (`main`, `_take`), `tests/test_compose.py`. Depends: none.
  - Tests: one, two and three `--env-file` keep order; `--env-file=x`, `--project-directory=d` behave like the separate form; `--env-file ""` and a trailing `--env-file` exit 64 naming the option; `exec --env-file` is still forwarded to `exec`.
  - Done: new tests pass; the existing `main`/dispatch tests are unchanged.
- [x] 2.2 Thread `env_files` (a tuple) through `_dispatch`, `_load_project` and `_normalizer_inputs`; extend `_require_builtin_subset_inputs` (several files, `COMPOSE_ENV_FILES`) and make `_load_env_file` fail for an explicit missing file (design D7) @worker:orchestrator
  - Priority: P0. Files: `src/container_docker_shim/compose.py`, `tests/test_compose.py`. Depends: 2.1.
  - Tests: the fake normalizer receives `--env-file a --env-file b` in order, before `--profile` as `build_argv` fixes it; the process environment still reaches the child; fallback: two files -> 64 with the install hint, `COMPOSE_ENV_FILES` -> 64, explicit missing file -> exit 1 `couldn't find env file`, missing default `.env` still silent, single file unchanged; `--project-directory` does not change how `--env-file` is forwarded.
  - Done: `python3 -m unittest tests.test_compose tests.test_compose_normalizer -v` passes.
- [x] 2.3 Add gated official-binary tests proving the shim equals Docker Compose for env files @worker:local
  - Priority: P0. Files: `tests/test_compose_official.py`, new fixture `tests/fixtures/compose/env-files/` (`compose.yaml`, `e1.env`, `e2.env`, `svc.env`, `dot-env`). Depends: 2.2.
  - Tests (skipped unless `docker-compose` is exactly 5.6.0): one file; two files; repeated variable across files; shell variable overriding files; missing file message; relative paths with and without `--project-directory`; `.env` ignored once `--env-file` is given; service `env_file` not used for interpolation; `COMPOSE_ENV_FILES` and the command line winning over it; shim `config` output equals direct `docker-compose config` output in each case.
  - Done: tests pass locally with `docker-compose` 5.6.0 and are reported skipped, with the reason, without it.

## 3. Mount compatibility (P1)

- [x] 3.1 Make the capability gate read the `mounts` policy instead of `MOUNT_OPTIONS`/`MOUNT_SUBKEYS`; `z`, `Z` and `bind.selinux` are refusals; `consistency` is accepted without effect @worker:local
  - Priority: P0. Files: `src/container_docker_shim/compose_capabilities.py`, `tests/test_compose_capabilities.py`. Depends: 1.1.
  - Tests: replace `test_modes_that_are_accepted` (`z`, `Z`, `ro,z` now refused with the SELinux reason, `ro`, `rw`, `cached`, `delegated` accepted); long syntax `bind.selinux`; named volume with `bind.selinux` (the normalizer shape); unknown nested key refused; refusals are not waivable by `CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED`.
  - Done: `python3 -m unittest tests.test_compose_capabilities -v` passes and `compose_capabilities` defines no mount-option table of its own.
- [x] 3.2 Replace the duplicated mount code in `compose.py`: pure `_service_mounts(service, project)` built on `mounts.mount_from_entry`/`check_short_modes`; delete `_NOOP_MOUNT_MODES`, `_mount_readonly`, `_is_host_path`, `_resolve_host_path`, `_join_volume`; keep `_ensure_named_volume` as the only effect; `_build_run_args` consumes the result (design D4, D5) @worker:orchestrator
  - Priority: P0. Files: `src/container_docker_shim/compose.py`, `tests/test_compose.py`. Depends: 1.1, 2.2.
  - Tests: `VolumeMountTranslationTests` keep their `--mount` outputs for `ro`, `rw`, `cached`, named and bind; the `z` rows become refusals; the normalizer-shaped entry `{type: volume, ..., bind: {selinux: Z}}` is refused; `_service_volume_keys` still lists only named volumes.
  - Done: `python3 -m unittest tests.test_compose -v` passes and `grep -n "_NOOP_MOUNT_MODES" src` finds nothing.
- [x] 3.3 Validate mounts in `_validate_plan`: call `_service_mounts` for every planned service, turn `MountSpecError` into `REFUSED:` lines, and convert any escaping `MountSpecError` to a 64 at the compose boundary @worker:orchestrator
  - Priority: P0. Files: `src/container_docker_shim/compose.py`, `tests/test_compose.py`. Depends: 3.1, 3.2.
  - Tests with the recording fake runtime: service `a` valid and `b` with `:Z` -> exit 64 and no `network create`, `volume create`, `build` or `run` call at all; source with a comma and a relative target -> `REFUSED:` and no traceback; `cmd_build` also stops before any build; all problems of all services are printed together.
  - Done: new tests pass; the failing scenario of `findings.md` F2a no longer prints `Creating ...`.
- [x] 3.4 Contract test CT2: one verdict per mount option across `docker run -v`, Compose short (built-in parser), Compose long (normalizer-shaped model) and the gate @worker:local
  - Priority: P0. Files: `tests/test_contract_mount_policy.py` (new, `TIER = "unit"`). Depends: 3.2.
  - Done: the test iterates the table in `mounts.py`, passes, and fails when a surface is made to disagree (assert with a patched copy).
- [x] 3.5 Classify `bind.create_host_path` and `consistency` against the real runtime: with a disposable `shimtest-` container, record what Apple 1.5.0 does with a missing bind source and set the table entry to accepted, accepted-without-effect or refused accordingly @worker:orchestrator @review:none
  - Priority: P1. Files: `src/container_docker_shim/mounts.py` (table entry only), `tests/test_live_fixtures.py` (one live test), `findings.md` (record the observation). Depends: 3.4.
  - Done: the live test passes under `SHIM_TIERS=live` and cleans up by name; the policy entry and the matrix wording (task 5.1) match the observation; without the live tier the test is reported skipped.
- [x] 3.6 Live and conformance fixtures for mounts: extend `m-readonly-volume` with long-syntax `read_only: true` mounts and a read-write control; add `r-selinux-refused` (`./data:/data:Z`) whose live test asserts exit 64, a `REFUSED:` line and no created object, and whose conformance test records that Docker Engine accepts it @worker:local
  - Priority: P0. Files: `tests/fixtures/compat/m-readonly-volume/`, `tests/fixtures/compat/r-selinux-refused/` (new), `tests/test_live_fixtures.py`, `tests/test_conformance_fixtures.py`. Depends: 3.3.
  - Done: with `SHIM_TIERS=live` the read-only writes fail and the control write succeeds on the shim, with `SHIM_TIERS=conformance` the same file behaves the same on Docker (SELinux row aside); both clean up every `shimtest-` object; default run reports both skipped with a reason.

## 4. Build secrets (P1)

- [x] 4.1 Add `Project.secrets`, replace `_build_secret_args` with a call into `compose_build_secrets` (id = `target` or `source`, absolute paths, notices printed as `NOT APPLIED:`) @worker:orchestrator
  - Priority: P0. Files: `src/container_docker_shim/compose.py`, `tests/test_compose.py`. Depends: 1.2, 1.3, 3.3.
  - Tests: the existing `build_args` cases keep passing through the built-in parser; the same projects built from a normalizer-shaped model (using the `build-secrets.json` fixture) produce identical arguments; `target` changes the id; a path with `$`; `uid`/`mode` produce a notice.
  - Done: `python3 -m unittest tests.test_compose -v` passes; `_run_build` output for a secret contains only ids, paths and variable names.
- [x] 4.2 Pre-flight secrets in `cmd_up` and `cmd_build` (design D6): structural problems in `_validate_plan`, file and variable checks for services that will build, all as `REFUSED:` lines before `_check_resources` and `_ensure_network` @worker:orchestrator
  - Priority: P0. Files: `src/container_docker_shim/compose.py`, `tests/test_compose.py`. Depends: 4.1.
  - Tests with the recording fake runtime: missing file, unset variable, undefined, `external`, ambiguous definition -> exit 64 and no `network create`, `volume create`, `build` or `run`; image present without `--build` and a missing file -> `up` proceeds; the same with a structurally invalid definition -> exit 64; runtime `secrets` plus build secret -> existing refusal still printed; a sentinel secret value set in the environment appears in no captured stdout, stderr or argument list.
  - Done: new tests pass; the F1c scenario in `findings.md` fails before any runtime call.
- [x] 4.3 Contract test CT3: generated secret definitions give arguments or a problem, never neither, and the official-shaped model and the built-in parser's model resolve identically @worker:local
  - Priority: P1. Files: `tests/test_contract_build_secrets.py` (new, `TIER = "unit"`). Depends: 4.2.
  - Done: the test passes and fails when the resolver silently drops an entry (assert with a patched resolver).
- [x] 4.4 Add the gated official-binary test for the reproduction project of P1: load it with the real normalizer and assert the build arguments @worker:local
  - Priority: P0. Files: `tests/test_compose_official.py`, new fixture `tests/fixtures/compose/build-secrets/`. Depends: 4.1, 2.3.
  - Tests (skipped unless `docker-compose` is exactly 5.6.0): `file` and `environment` secrets, `target` rename, relative file resolved with and without `--project-directory`, undefined secret rejected by the official binary itself, runtime secret still refused by the gate.
  - Done: tests pass locally with 5.6.0 and are reported skipped with the reason without it.
- [x] 4.5 Live and conformance fixture `q-build-secrets`: a Dockerfile that reads a file secret and an environment secret with a renamed `target` through `RUN --mount=type=secret` and writes only `ok` into the image; live asserts the build succeeds through the shim and the image runs; conformance asserts the same file builds on Docker; the live test also records, without relying on it, what Apple does when the variable is unset @worker:local
  - Priority: P0. Files: `tests/fixtures/compat/q-build-secrets/` (new), `tests/test_live_fixtures.py`, `tests/test_conformance_fixtures.py`, `findings.md` (record the unset-variable observation). Depends: 4.2, 3.6.
  - Done: with `SHIM_TIERS=live` and then `conformance` both pass using the identical, unmodified fixture, the dummy secret value appears in no captured output, and the images created are removed by exact name; default run reports skipped with a reason.

## 5. Matrix, documentation and integration

- [x] 5.1 Matrix and docs: `compose volumes.z`, `volumes.Z` UNSUPPORTED, add `compose volumes.selinux`; evidence levels for `volumes.ro`, `volumes.read_only`, `build.secrets` (official + live), repeated `--env-file` (official), `bind.create_host_path` per 3.5; README Compose section (SELinux migration, build secret ids and `target`, environment secrets must be exported, repeated `--env-file`, fallback limits); `docs/testing.md` fixture list @worker:orchestrator
  - Priority: P0. Files: `tests/compat/matrix.json`, `README.md`, `docs/testing.md`, `tests/test_docs.py` if a rule needs updating. Depends: 2.3, 3.5, 3.6, 4.4, 4.5.
  - Done: `python3 scripts/render_compat_table.py --check README.md` exits 0 and `python3 -m unittest tests.test_docs tests.test_compat_matrix tests.test_compat_matrix_schema -v` passes.
- [x] 5.2 Integration: run the default suite, then the live and conformance tiers where the machine allows, validate the change, and confirm no attribution trailers @worker:orchestrator @review:none
  - Priority: P0. Files: none (report only). Depends: 5.1.
  - Done: `python3 -m unittest discover -s tests -v` passes; the report states per tier "ran", or "not run" with the reason; `openspec validate harden-compose-normalization-and-mounts --strict` reports valid; `git log --format=%B @{u}..HEAD | grep -iE "claude|anthropic|co-authored"` returns nothing for my commits.

## Workflow follow-up

- Review with `/opsx:verify`, then archive with `/opsx:archive` once the live and conformance results are recorded.
- Commit per scope (`mounts`, `compose/build-secrets`, `compose/env-file`, `compose/normalizer`, `docs`) following the repository's scoped-commit history.
