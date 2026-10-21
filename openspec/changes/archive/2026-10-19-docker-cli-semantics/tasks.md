# Tasks

Conventions for every task:

- Python 3.9 standard library only, `from __future__ import annotations`, unittest only; tests are top-level modules in `tests/` with a module-level `TIER`; live tests are opt-in (`SHIM_TIERS=live`), use `shimtest-` names, clean up by exact name, never prune.
- Each task lists `Priority` (P0 must ship, P1 should ship), `Files`, `Depends` and `Done`.
- Matrix entries land with the behaviour they describe; regenerate the README block with `python3 scripts/render_compat_table.py --write README.md`.
- Do not stage `AGENTS.md`.

## 1. Spikes (live or on a Docker host; results in `findings.md` of this change)

- [x] 1.1 Capture the reference output of `docker compose ps --format json -a` and of the default table from the real Docker Compose release used for conformance (project with a running, an exited-with-code-3 and a created container, plus an empty project); save the raw outputs under `tests/fixtures/compat/ps-reference/` and record the document layout (array or one object per line) @worker:orchestrator @review:none
  - Priority: P0. Files: `tests/fixtures/compat/ps-reference/`, `findings.md`. Depends: none.
  - Done: the fixture files exist, redacted of host names, and `findings.md` states the layout and the field types.
- [x] 1.2 C1: with a disposable container run `container run --cpus 0.5`, `--cpus 1.5` and `--cpus 2`, record accepted/rejected and the vCPU count seen inside (`nproc`) @worker:orchestrator @review:none
  - Priority: P1. Files: `findings.md`. Depends: none.
  - Done: findings.md records each result and the decision for D2.
- [x] 1.3 C2 and C3: record how Apple treats an `--env-file` with a bare `KEY` line and a `#` comment, and which `container kill -s` spellings (`TERM`, `SIGTERM`, `15`) deliver SIGTERM @worker:orchestrator @review:none
  - Priority: P1. Files: `findings.md`. Depends: none.
  - Done: findings.md records the observed behaviour for each case.
- [x] 1.4 Run `container run --mount type=bind,source=<relative path>,target=/t` and `-v name:/t` with a disposable name to record what Apple does with the current wrong translation (error, or a bind of `cwd/name`) @worker:orchestrator @review:none
  - Priority: P1. Files: `findings.md`. Depends: none.
  - Done: findings.md records the result and whether a directory was created on the host (and removes it if so).

## 2. Option semantics (run, create, build)

- [x] 2.1 Refuse `-c`, `--cpu-shares` and the combined forms (`-c2`) in `_parse_run_options` and `cmd_build` with a message naming both meanings; remove the `-c` -> `--cpus` branches; fix the alias table in `tests/test_compat_matrix.py` and the matrix notes
  - Priority: P0. Files: `cli.py`, `tests/test_cli.py`, `tests/test_compat_matrix.py`, `tests/test_option_sets.py`, `tests/compat/matrix.json`. Depends: none.
  - Tests: `run -c 2`, `create -c2`, `build -c 512` exit 64 with no runtime call; `--cpus 2` unchanged.
  - Done: `python3 -m unittest tests.test_cli tests.test_compat_matrix tests.test_option_sets -v` passes.
- [x] 2.2 Refuse `--runtime`; refuse `--network none|host|container:*`; map `bridge`/`default` to Apple's default network; keep named networks verbatim
  - Priority: P1. Files: `cli.py`, `tests/test_cli.py`, `tests/compat/matrix.json`. Depends: none.
  - Done: tests for each value pass; matrix entries updated.
- [x] 2.3 Memory and CPU values: convert bare byte counts to Apple units, refuse sub-MiB and non-multiples, and apply the C1 decision for fractional CPUs
  - Priority: P1. Files: `cli.py` (`_normalize_memory`, `_normalize_cpus`), `tests/test_cli.py`. Depends: 1.2.
  - Done: table-driven tests of inputs to argv or refusal pass.
- [x] 2.4 Add `tests/option_semantics.py` (the D1 table) and `tests/test_option_semantics.py` that derives the risky set from `cli.py` and fails for any option without a row; add the "differing meanings" note convention to matrix entries
  - Priority: P1. Files: `tests/option_semantics.py` (new), `tests/test_option_semantics.py` (new, `TIER = "unit"`), `tests/compat/matrix.json`. Depends: 2.1, 2.2.
  - Done: the test passes, and temporarily adding a new short alias to a set makes it fail naming the option (check once).
- [x] 2.5 Label Apple-only options (`--arch`, `--no-dns`, `build --cpus`) as extensions and justify `build --rm/--force-rm` in the matrix and README; regenerate the README block
  - Priority: P1. Files: `tests/compat/matrix.json`, `README.md`. Depends: 2.4.
  - Done: `python3 scripts/render_compat_table.py --check README.md` exits 0 and `python3 -m unittest tests.test_docs -v` passes.

## 3. Mounts

- [x] 3.1 Add `src/container_docker_shim/mounts.py` with the source classifier, the `--mount` parser (aliases, types, keys) and the emitters for bind, volume and tmpfs, shared by the CLI; use it from `_parse_run_options`
  - Priority: P1. Files: `mounts.py` (new), `cli.py`, `tests/test_mounts.py` (new, `TIER = "unit"`), `tests/test_cli.py`. Depends: 1.4.
  - Tests: named volume, bind with `./`, `/abs`, `~`, `ro`/`rw`, unknown mode, `--mount` aliases and unknown keys, `type=tmpfs`.
  - Done: new tests pass; existing bind tests unchanged.
- [x] 3.2 Named volumes in `docker run -v` are created on demand and mounted by the form chosen by spike S1 of `compose-capability-gate`; `:ro` on a volume is refused until S1 shows enforcement
  - Priority: P1. Files: `cli.py`, `mounts.py`, `tests/test_cli.py`, `tests/test_live_fixtures.py` (a live case with a `shimtest-` volume). Depends: 3.1; S1 (task 1.1 of `compose-capability-gate`).
  - Done: the live case shows the file inside a named volume and no host directory; unit tests pass.

## 4. Signals

- [x] 4.1 Normalise `kill -s` / `stop -s` signal spellings per C3 and refuse invalid names
  - Priority: P1. Files: `cli.py`, `tests/test_cli.py`, `tests/test_signals.py`, `tests/test_live_fixtures.py`. Depends: 1.3.
  - Done: tests for `TERM`, `SIGTERM`, `15`, invalid; a live case delivers SIGTERM to a `shimtest-` container.

## 5. compose ps

- [x] 5.1 Implement `render_table` and `render_json` as pure functions in a new `compose_ps.py` from the reference fixture of 1.1; add `--format`, `--status`, `--services`, `-q`, `-a`, service arguments, and exit-64 for templates in the dispatcher
  - Priority: P0. Files: `compose_ps.py` (new), `compose.py` (`cmd_ps`, `ps` branch), `tests/test_compose_ps.py` (new, `TIER = "unit"`), `tests/test_compose.py`. Depends: 1.1.
  - Tests: the three states, empty project, `--status`, `--services`, `-q`, template refusal, runtime failure leaves standard output empty.
  - Done: new tests pass; the JSON output of the fake-runtime case has the same keys and value types as the reference fixture.
- [x] 5.2 Extend the consumer contracts: add the `compose ps --format json` fields Hermes and OpenClaw read to their tests and add a generic-consumer contract test (`tests/test_agent_contract.py`)
  - Priority: P0. Files: `tests/test_hermes_contract.py`, `tests/test_openclaw_contract.py`, `tests/test_agent_contract.py`. Depends: 5.1.
  - Done: the three modules pass; renaming a JSON field in `compose_ps.py` makes them fail (check once).
- [x] 5.3 Add matrix entries of kind `format` for `docker compose ps --format json|table`, the template refusal and the other declared formats; add the docs check of D6 to `tests/test_docs.py`; correct the README paragraph "Behavior for scripts and agents"
  - Priority: P0. Files: `tests/compat/matrix.json`, `tests/compat_matrix.py` (schema allows kind `format`), `tests/test_docs.py`, `README.md`. Depends: 5.1.
  - Done: `python3 -m unittest tests.test_docs tests.test_compat_matrix_schema tests.test_compat_matrix -v` passes and the README states no unsupported format.
- [x] 5.4 Live and conformance cases: `compose ps --format json -a` on fixture L (partial failure) and fixture K (short-lived) compares state and exit code with the reference layout; conformance runs the same file on Docker
  - Priority: P1. Files: `tests/test_live_fixtures.py`, `tests/test_conformance_fixtures.py`. Depends: 5.1.
  - Done: Docker conformance compares the numeric exit code with the reference. Apple container 1.5.0 exposes no numeric exit-code field, so the live shim case verifies an explicit exit-64 refusal for exited rows; the listing emits no misleading zero and cleans up its disposable project.

- [x] 5.5 Accept `docker version --format|-f` (`json`, `{{.Client.Version}}`, `{{.Server.Version}}`) through the bounded formatter and refuse other templates; add the contract test and matrix entry
  - Priority: P1. Files: `cli.py` (`cmd_version`), `tests/test_cli.py`, `tests/test_agent_contract.py`, `tests/compat/matrix.json`. Depends: none.
  - Done: the three scenarios of "Version probes" are named tests that pass; plain `docker version` output is unchanged.

## 6. Integration

- [x] 6.1 Run the default suite and the live tier for the modified areas; record the commands, counts and tiers not run in `findings.md` @worker:orchestrator @review:none
  - Priority: P0. Files: `findings.md`. Depends: all above.
  - Done: `findings.md` lists commands, counts, skipped tiers by name and unresolved failures.

## Workflow follow-up

- Archive after review; the deltas modify `agent-cli-contract` and `compatibility-matrix` and add two capabilities.

## Independent review corrections

- [x] R1 Refuse `compose ps --format json` when an included exited row has no verified numeric exit code; preserve table, quiet and services output. Update the compose-ps delta, findings, design and K/L live expectations.
  - Done: supported Apple container 1.5.0 list/inspect shapes were checked; focused tests, full unit suite and a scoped live smoke check passed, and the probe resources were removed.
- [x] R2 Refuse Docker `-v` SELinux relabel modes `z`/`Z` unless the supported macOS runtime semantics are verified; add focused parser/CLI tests and keep Docker-only behavior explicit in the compatibility matrix.
  - Done: verified Apple container 1.5.0 cannot represent SELinux relabeling; `python3 -m unittest tests.test_mounts tests.test_cli -v` passed (122 tests). Compatibility-matrix wording is handled in R4.
- [x] R3 Validate the complete `docker run` argv, including image and trailing options, before creating named volumes; prove invalid invocations leave no volume behind and valid invocations still create the volume on demand.
  - Done: deferred named-volume inspect/create until full argv and image validation pass; `python3 -m unittest tests.test_cli.RunMountTranslationTests -v` passed (8 tests), including both invalid cases and valid on-demand creation.
- [x] R4 Correct stale `run --cpus` and `run --mount` compatibility notes and add a documentation consistency check; regenerate the README compatibility table and update the integration findings with the final review-correction test results.
  - Done: matrix notes now match integer-only `--cpus`, validated `--mount`, and refused SELinux `z/Z` modes; the README block was regenerated. `PYTHONPATH=tests python3 -m unittest tests.test_docs tests.test_compat_matrix_schema tests.test_compat_matrix -v` passed (43 tests), and `python3 scripts/render_compat_table.py --check README.md` passed. The full suite and final counts are recorded in findings.md.
- [x] R5 Shorten the `compose ps` JSON requirement to satisfy strict OpenSpec validation while preserving its refusal behavior and scenarios.
  - Done: moved edge-case detail to scenarios; `openspec validate docker-cli-semantics --strict` reports the change valid.
- [x] R6 Reject `docker create -d/--detach` before named-volume side effects, while preserving valid on-demand named-volume creation.
  - Done: `python3 -m unittest tests.test_cli.RunMountTranslationTests -v` passed (10 tests), including invalid `create -d` with no side effects and valid create/run volume creation.
