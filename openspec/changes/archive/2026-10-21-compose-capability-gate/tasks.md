# Tasks

Conventions for every task (same as the archived `compat-matrix-and-tests`):

- Python 3.9 standard library only, `from __future__ import annotations`, unittest only. Tests are top-level modules in `tests/` with a module-level `TIER` line; live tests are opt-in (`SHIM_TIERS=live`), name objects `shimtest-<topic>-<short uuid>`, clean up by exact name, never prune.
- Matrix entry schema: `{"id","kind","status","apple","evidence","test","note"}`; a table key without an entry fails `tests/test_compat_matrix.py`, so entries land in the same task as the key.
- Every task lists `Priority` (P0 must ship, P1 should ship), `Files` (expected, not exhaustive), `Depends` (task ids) and `Done` (the observable check). Tasks within one group touch different files unless stated, so they can go to different workers.
- Do not stage `AGENTS.md`. Do not touch `docker-compose` installation or any other project's containers.

## 1. Spikes (live, disposable; results recorded in `findings.md` under "Spike results")

- [x] 1.1 S1: with a disposable `shimtest-` volume, run `container run` with `-v name:/t:ro` and with `--mount type=volume,source=name,target=/t,readonly`; for each, try to write inside the container and record which form (if any) rejects the write @worker:orchestrator @review:none
  - Priority: P1. Files: `findings.md`. Depends: none.
  - Done: findings.md states for each form the exit status of the write and the chosen mapping for task 4.1, or "no enforcement" (then 4.1 refuses).
- [x] 1.2 S2: with a disposable file, run `container run --mount type=bind,source=<file>,target=/run/secrets/x,readonly` (a single file, not a directory) and read it as a non-root user; record whether single-file mounts work and which mode and owner the process sees @worker:orchestrator @review:none
  - Priority: P1. Files: `findings.md`. Depends: none.
  - Done: findings.md records yes/no for single-file mounts and the observed mode and owner; this decides whether a follow-up change implements secrets alternative A.

## 2. Classification table and waiver policy

- [x] 2.1 Add `src/container_docker_shim/compose_capabilities.py` with `Rule` (`status`, `effect`, `waivable`, `reason`), the tables `SERVICE_KEYS`, `BUILD_KEYS`, `MOUNT_OPTIONS`, `RESOURCE_ATTRIBUTES` covering every key named in the specs (all Compose service keys, including those that are supported today), and a pure `validate(project, services) -> Report` @worker:orchestrator
  - Priority: P0. Files: `compose_capabilities.py` (new), `tests/test_compose_capabilities.py` (new, `TIER = "unit"`). Depends: none.
  - Tests: one case per refusal class (unknown key, `network_mode` values, `secrets`/`configs`, restriction keys, behaviour keys, `x-` extension, supported keys, `restart: "no"` silent, `deploy.replicas: 1` silent); a test that every key in `docker-compose config --format json` output of the fixtures is classified.
  - Done: `python3 -m unittest tests.test_compose_capabilities -v` passes and no module other than the new one is imported by it besides `security`.
- [x] 2.2 In `security.py` add `waivable` to `OptionRule`, mark `network-none` (and the new `network_mode` rule) non-waivable, drop `hostname` and `add-host` from the waivable names, make `decide` return a `refuse` that says isolation requests cannot be waived, and add `unknown_waivers(environ)` returning names that match no waivable rule @worker:local
  - Priority: P0. Files: `security.py`, `tests/test_security.py`. Depends: none.
  - Tests: waiving `network-none` still refuses; waiving `pids-limit` skips; a typo is reported by `unknown_waivers`; the README Security table test still derives from `RULES`.
  - Done: `python3 -m unittest tests.test_security -v` passes.
- [x] 2.3 Add the message helpers `refused(...)` and `not_applied(...)` (prefixes `REFUSED:` and `NOT APPLIED:`, standard error only) in `compose_capabilities.py` and use them in `cli.py` for the `run`/`create` waiver path and the unknown-waiver warning @worker:local
  - Priority: P0. Files: `compose_capabilities.py`, `cli.py` (the `decide` call in `_parse_run_options` and the entry point warning), `tests/test_cli.py`. Depends: 2.1, 2.2.
  - Tests: `run -d` with a waived option prints only the container id on standard output and one `NOT APPLIED:` line on standard error; a refusal starts with `REFUSED:`.
  - Done: `python3 -m unittest tests.test_cli tests.test_security -v` passes.

## 3. Validation before provisioning

- [x] 3.1 In `cmd_up` and `cmd_build`, call `compose_capabilities.validate` right after the plan is known and before `_ensure_network`, `_resolve_image` and any create call; move `_enforce_run_security` and `_validate_dependencies` into the same step so one run reports every problem; print notices before provisioning @worker:codex
  - Priority: P0. Files: `compose.py` (`cmd_up`, `cmd_build`, `_enforce_run_security`, `_validate_dependencies`), `tests/test_compose.py`. Depends: 2.1, 2.3.
  - Tests: with the fake runtime, a refused project records no `network create`, `volume create`, `build` or `run` call; an already running project is untouched when an edit introduces a refusal; two problems are both reported in one run.
  - Done: new tests pass and `python3 -m unittest tests.test_compose -v` still passes.
- [x] 3.2 Replace `_warn_unsupported` and the `UNSUPPORTED_SERVICE_KEYS`/`SECURITY_OPTION_KEYS` split by the table, keeping `SUPPORTED_SERVICE_KEYS` and `UNSUPPORTED_SERVICE_KEYS` as derived views so the coverage tests keep working; add a `compose <key>` matrix entry for every key the table classifies and regenerate the README block @worker:codex
  - Priority: P0. Files: `compose.py`, `tests/compat/matrix.json`, `README.md` (generated block only), `tests/test_option_sets.py`, `tests/test_compat_matrix.py`. Depends: 3.1.
  - Done: `python3 scripts/render_compat_table.py --check README.md` exits 0 and `python3 -m unittest tests.test_compat_matrix tests.test_option_sets -v` passes.
- [x] 3.3 `network_mode` (every value) and the isolation keys refused, not waivable; fake-runtime tests for `none`, `host`, `service:x`, `container:x` and for the waiver having no effect @worker:local
  - Priority: P0. Files: `compose_capabilities.py`, `tests/test_compose_capabilities.py`, `tests/test_compose.py`. Depends: 3.1.
  - Done: the scenarios of `compose-capability-validation` "Network isolation requests are refused" are covered by named tests that pass.
- [x] 3.4 `secrets` and `configs` refused with the new message (Compose feature, not Swarm) and fix the stale reason text and README wording; ensure no secret value is read by the shim @worker:local
  - Priority: P1. Files: `compose_capabilities.py`, `README.md` (Compose section prose), `tests/test_compose_capabilities.py`. Depends: 3.1.
  - Done: tests prove the file named by `secrets.<n>.file` is never opened by the shim (fake `open` guard) and `up` exits 64 before any create call.
- [x] 3.5 Top-level resource attributes (`driver`, `driver_opts`, `ipam`, `enable_ipv6`, `attachable`) refused, `internal` and `labels` honoured @worker:local
  - Priority: P1. Files: `compose_capabilities.py`, `compose.py` (`_ensure_network`), `tests/test_compose_capabilities.py`. Depends: 3.1.
  - Done: a volume with `driver_opts` exits 64 without creating a volume; `internal: true` still passes `--internal`.

## 4. Mounts

- [x] 4.1 Rewrite mount translation in `_classify_and_translate_volume` to emit one `--mount` form per the S1 result, carrying `readonly` for `read_only: true` and `:ro` on named volumes and binds, accept `rw`, `z`, `Z`, `cached`, `delegated` as no-ops, refuse every other option, and refuse the mount when S1 found no enforcement @worker:codex
  - Priority: P1. Files: `compose.py`, `tests/test_compose.py`. Depends: 1.1, 3.1.
  - Tests: table of short and long syntax inputs to expected argv; unsupported options exit 64 naming service and target; relative and absolute bind paths unchanged.
  - Done: new tests pass; existing bind-mount tests unchanged.
- [x] 4.2 Add fixtures `tests/fixtures/compat/m-readonly-volume/` (named volume and bind, both read-only, a service that tries to write and prints the result) and `tests/fixtures/compat/n-network-none/` (a service with `network_mode: none`), with the `# tiers:` and `# images:` header, and tests: live asserts the write fails and `up` on `n-network-none` exits 64 without creating objects; conformance asserts the same write fails and that `n-network-none` has no connectivity on real Docker @worker:local
  - Priority: P1. Files: `tests/fixtures/compat/m-*`, `tests/fixtures/compat/n-*`, `tests/test_live_fixtures.py`, `tests/test_conformance_fixtures.py`. Depends: 4.1, 3.3.
  - Done: with `SHIM_TIERS=live` the new tests pass and leave no `shimtest-` object; the default run lists them as skipped with a reason.

## 5. Build keys

- [x] 5.1 Make `_run_build` forward `target`, `labels`, `no_cache`, `pull`, `secrets` and `ssh: default` through the same argument builder `docker build` uses, and refuse other sub-keys through the table @worker:codex
  - Priority: P1. Files: `compose.py`, `cli.py` (extract the shared builder from `cmd_build`), `tests/test_compose.py`, `tests/test_cli.py`. Depends: 3.1.
  - Tests: a service with `build.target` produces `--target`; `labels`, `no_cache`, `pull` map to flags; `cache_from` exits 64 naming the sub-key.
  - Done: new tests pass; a live check builds a two-stage Dockerfile with `target` and `docker image inspect` shows the selected stage.

## 6. Documentation and integration

- [x] 6.1 README: describe the validation phase, the `REFUSED:`/`NOT APPLIED:` tokens, the non-waivable isolation requests, the secrets/configs decision and the BREAKING note; correct the Compose paragraph that says unknown keys "are parsed but ignored" @worker:orchestrator @review:none
  - Priority: P1. Files: `README.md`, `docs/testing.md`. Depends: 3.2 to 3.5, 4.1.
  - Done: `python3 -m unittest tests.test_docs -v` passes and no sentence in the README says a classified-away key is ignored.
- [x] 6.2 Integration: run the whole default suite, `python3 scripts/validate-runtime.py`, and the live tier for fixtures A to L plus M and N; record the results and "not run" tiers in `findings.md`
  - Priority: P0. Files: `findings.md`. Depends: all above. @worker:orchestrator @review:none
  - Done: the recorded summary lists the commands, counts, skipped tiers by name, and any failure that was not resolved.

## Workflow follow-up

- Archive the change after review and merge its deltas into `compose-normalization`, `compose-volume-safety` and `run-security-options`.
- If spike 1.2 passes, open a change for secrets and configs alternative A.
