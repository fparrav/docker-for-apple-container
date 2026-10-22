# Tasks

Conventions for every task:

- Python 3.9 standard library only, `from __future__ import annotations`, unittest only. Tests are top-level modules in `tests/` with a module-level `TIER`; live tests are opt-in (`SHIM_TIERS=live`), name objects `shimtest-<topic>-<short uuid>`, clean up by exact name, never prune. Run `python3 -m unittest discover -s tests -v` before closing a group.
- Each task lists `Priority` (P0 must ship, P1 should ship), `Files`, `Depends` and `Done`. `compose.py` is a hotspot: every task that edits it is chained through `Depends`, so no two run at once. The same holds for `scripts/test_report.py` and `scripts/validate-runtime.py`.
- Matrix entries (`tests/compat/matrix.json`) change in group 6; regenerate the README block with `python3 scripts/render_compat_table.py --write README.md`. A matrix row may claim `live` or `conformance` only when a test in this change proves it.
- The supported runtime stays `container 1.5.0`; do not touch `SUPPORTED_CONTAINER_VERSION`. Do not stage `openspec/` or `AGENTS.md`.
- Real Docker for the conformance tier is `/usr/local/bin/docker` (plain `docker` is the shim): `SHIM_TIERS=conformance SHIM_CONFORMANCE_DOCKER=/usr/local/bin/docker`. Live tests that create objects remove them by exact name, and a task that runs the live tier checks `container volume list`, `container list --all` and `container network list` for leftovers afterwards.
- Evidence files are produced only by the release procedure. No task in this change commits a file under `docs/evidence/` other than test fixtures built in a temporary directory.

## 1. Refuse `rbind` (independent of everything else)

- [x] 1.1 Make `local_bind` refuse `rbind` with a message that points to `bind`, and drop the unused `recursive` field (design D5) @worker:local
  - Priority: P0. Files: `src/container_docker_shim/compose_resource_compat.py`, `tests/test_compose_resource_compat.py`. Depends: none.
  - Tests: `o: rbind` and `o: rbind,ro` return a `Problem` that names `rbind` and contains `bind`; `o: bind`, `bind,ro`, `bind,rw` still translate; `o: ro` alone is still refused; nothing else in `src/` reads `recursive` (`grep`).
  - Done: `python3 -m unittest tests.test_compose_resource_compat -v` passes with no filesystem or runtime access.
- [x] 1.2 Update the tests that expected `rbind` to be accepted, and prove the refusal end to end @worker:local
  - Priority: P0. Files: `tests/test_compose_capabilities.py` (loop at the `bind,ro`/`rbind` case), `tests/test_compose.py` (the `write_local_bind_compose("rbind", ...)` test), `tests/test_contract_resource_options.py` (the `recursive bind` row). Depends: 1.1.
  - Tests: the gate and `up` agree: `o: rbind` exits 64 with a `REFUSED:` line naming the volume and `rbind`, nothing is created (no `volume create`, no `run`); `--dry-run up` reports the same refusal.
  - Done: `python3 -m unittest tests.test_compose_capabilities tests.test_contract_resource_options tests.test_compose -v` passes.

## 2. `--require` and fixture coverage

- [x] 2.1 Add `declared_tests(tier)` to `scripts/test_report.py`: AST scan of `tests/test_*.py` counting `test_*` functions decorated with `tiers.live_tier(...)` or `tiers.conformance_tier(...)` in modules whose module-level `TIER` equals the tier (design D6) @worker:local
  - Priority: P0. Files: `scripts/test_report.py`, `tests/test_test_report.py`. Depends: none.
  - Tests: a temporary tests directory with decorated and undecorated functions, a class method, and a module that fails to parse gives the right counts per tier; the real repository reports a non-zero count for `conformance` and for `live`.
  - Done: `python3 -m unittest tests.test_test_report -v` passes.
- [x] 2.2 Add `--require <tier>[,<tier>]` to `scripts/test_report.py` with exit 3 and a stderr explanation (design D6) @worker:orchestrator
  - Priority: P0. Files: `scripts/test_report.py`, `tests/test_test_report.py`, `docs/testing.md`. Depends: 2.1.
  - Tests with injected records and declared counts: tier not enabled -> 3; every record not run -> 3; one skipped -> 3 naming it; records pass but `declared` is larger -> 3 with both counts; all passed and counts equal -> 0; a failed test -> 1 (not 3); `--require unit` -> usage error 2; the report file and summary are still written when the requirement is unmet.
  - Done: `python3 -m unittest tests.test_test_report -v` passes; `docs/testing.md` documents the flag and the exit 3; the coordinator runs `SHIM_TIERS=conformance SHIM_CONFORMANCE_DOCKER=/usr/local/bin/docker python3 scripts/test_report.py --require conformance` and sees exit 0 with the number of conformance tests, then the same command with `SHIM_CONFORMANCE_DOCKER` unset and sees exit 3.
- [x] 2.3 Use `--require conformance` in the `conformance` job of `ci.yml` and assert it in the workflow tests (design D9) @worker:local
  - Priority: P0. Files: `.github/workflows/ci.yml`, `tests/test_workflows.py`. Depends: 2.2.
  - Tests: the conformance job's report step contains `--require conformance`; the later report-recording step keeps `if: always()`; no number of tests appears in the workflow.
  - Done: `python3 -m unittest tests.test_workflows -v` passes.
- [x] 2.4 Unit test that every fixture with `conformance` in its header is used by a conformance test (design D7) @worker:local
  - Priority: P1. Files: `tests/test_fixture_coverage.py` (new, `TIER = "unit"`). Depends: none.
  - Tests: reads the first line of each `tests/fixtures/compat/*/compose.yaml`; for headers that list `conformance`, the directory name appears in `tests/test_conformance_fixtures.py`; a synthetic fixture directory without a test makes the check fail naming it; `ps-reference` (no header) is not required.
  - Done: `python3 -m unittest tests.test_fixture_coverage -v` passes on the repository as it is.

## 3. Anonymous volume lifecycle

- [x] 3.1 Accept `-V` / `--renew-anon-volumes` in `up` and carry a `renew_anonymous` value from `cmd_up` to `_create_service` (design D3) @worker:orchestrator
  - Priority: P0. Files: `src/container_docker_shim/compose.py`, `tests/test_compose.py`. Depends: none.
  - Tests: the flag is parsed in both spellings; `--dry-run up -V` runs and creates nothing; `-V` without any recreate changes nothing; `-V` is rejected by commands other than `up` as any unknown flag is.
  - Done: `python3 -m unittest tests.test_compose -v` passes; no behaviour change yet without the flag.
- [x] 3.2 Add `_discard_anonymous_volume` with the label check and the in-use failure (design D2) @worker:orchestrator
  - Priority: P0. Files: `src/container_docker_shim/compose.py`, `tests/test_compose.py`. Depends: 3.1.
  - Tests with the fake runtime: a labelled volume is inspected then removed; a volume with the deterministic name and without the three labels exits non-zero naming it and `volume rm` is never called; `volume rm` failing exits non-zero naming the volume and service; an absent volume is a no-op.
  - Done: the new tests pass and no `volume rm` is ever issued for a name that does not come from the model.
- [x] 3.3 Decide create versus recreate in `cmd_up` and apply it in `_ensure_anonymous_volume` (design D1) @worker:orchestrator
  - Priority: P0. Files: `src/container_docker_shim/compose.py`, `tests/test_compose.py`. Depends: 3.2.
  - Tests with the fake runtime, one per row of the D1 table: `keep` and `start` never touch the volume; `recreate` without `-V` reuses it and does not seed; `recreate` with `-V` removes, creates and seeds; `create` with an existing labelled volume removes and seeds; `create` with none seeds; a dependency-driven recreate reuses the volume; `--force-recreate` reuses it; a container deleted outside Compose creates a fresh volume; a seed failure still removes the new volume and fails; a named volume in the same service is never touched.
  - Done: `python3 -m unittest tests.test_compose -v` passes and the existing anonymous volume tests are updated to the new rule, not deleted.
- [x] 3.4 Extend the live test for fixture S with the lifecycle sequence (design D4) @worker:orchestrator
  - Priority: P0. Files: `tests/test_live_fixtures.py`. Depends: 3.3.
  - Tests: marker; `up -d` with `REV=2` keeps it; `up -d -V` with `REV=3` drops it and copy-up is back; marker again; `down`; `up -d`; the marker is gone; `down -v` leaves no volume of the project. The fixture file is not edited.
  - Done: `SHIM_TIERS=live python3 -m unittest tests.test_live_fixtures -k fixture_s -v` passes on `container 1.5.0` (the coordinator runs it) and the leftover check shows no `shimtest-` objects.
- [x] 3.5 Extend the conformance test for fixture S with the same sequence and drop the "shim keeps them by design" comment (design D4) @worker:local
  - Priority: P0. Files: `tests/test_conformance_fixtures.py`. Depends: 3.3.
  - Tests: identical sequence and assertions as 3.4, except that the listing over the missing path is empty (Docker volumes carry no `lost+found`).
  - Done: with `SHIM_TIERS=conformance SHIM_CONFORMANCE_DOCKER=/usr/local/bin/docker` the test passes on Docker Desktop (the coordinator runs it) with no `shimtest-`/`conf-` leftovers in `docker volume ls`.

## 4. Release evidence

- [x] 4.1 Add the evidence module and `--evidence` to `scripts/validate-runtime.py` (design D8, "Producing it") @worker:orchestrator
  - Priority: P0. Files: `scripts/validate-runtime.py`, `tests/test_validate_runtime.py`. Depends: 2.1.
  - Tests with an injected runtime, injected `run_live`, an injected git helper and a temporary output directory: success writes a file with the schema fields, a `tree_hash` equal to the sha256 of the three tree lines, counts that add up and `declared == ran`; any failed, skipped or not-run test writes nothing and keeps the existing exit status; `--evidence --skip-live` exits 2; a dirty tracked or untracked file under `src`, `scripts` or `tests` refuses and names the directory; the file name carries the version and a UTC timestamp.
  - Done: `python3 -m unittest tests.test_validate_runtime -v` passes with no runtime; the coordinator runs `python3 scripts/validate-runtime.py --evidence --skip-live` and sees exit 2.
- [x] 4.2 Add `scripts/verify_release_evidence.py` with tag classification, evidence checks, `--print-channel` and `--notes` (design D8, "Verifying") @worker:orchestrator
  - Priority: P0. Files: `scripts/verify_release_evidence.py` (new), `tests/test_verify_release_evidence.py` (new, `TIER = "unit"`, imports the script with `importlib`). Depends: 4.1.
  - Tests with a temporary git repository and temporary evidence files, one per scenario of the `release-evidence` spec: matching evidence -> 0; version differs; tree hash differs after a code commit but not after a README-only or evidence-only commit; older than 30 days; dated in the future; `ran` zero; skipped, not-run or failed non-zero; `ran != passed`; no files; malformed JSON; missing field; counts that do not add up; newest of several files is the one judged; `v1.2.3rc1`, `v1.2.3a1`, `v1.2.3b1`, `v1.2.3.dev1` -> 0 without reading evidence and channel `prerelease`; `v1.2.3-spike` and `v1.2.3.post1` -> 3; `--notes` for a final tag names the file, version, date and counts, and for a pre-release contains the sentence that evidence was not required.
  - Done: `python3 -m unittest tests.test_verify_release_evidence -v` passes with no network; the script depends on nothing outside the standard library and reads `SUPPORTED_CONTAINER_VERSION` from `cli.py` by text.
- [x] 4.3 Wire the verifier into `release.yml` and publish pre-releases as such (design D9) @worker:local
  - Priority: P0. Files: `.github/workflows/release.yml`, `tests/test_workflows.py`. Depends: 4.2.
  - Tests: the evidence step is in the `checks` job and passes the tag through `env`, never inline in the shell line; `checks` keeps `contents: read`; no job uses `self-hosted`; the `release` job computes the channel, appends the notes section, and passes `prerelease` to the release action; action pins and permissions are unchanged.
  - Done: `python3 -m unittest tests.test_workflows -v` passes.
- [x] 4.4 Document the maintainer procedure and the limits of the evidence (design D10) @worker:local
  - Priority: P0. Files: `docs/releasing.md`, `docs/testing.md`. Depends: 4.2.
  - Tests: `tests/test_docs.py` is extended so `docs/releasing.md` mentions `--evidence`, `docs/evidence/`, the 30-day limit, pre-release tags, and that the file is not signed.
  - Done: `python3 -m unittest tests.test_docs -v` passes; the sequence in the document matches what the verifier checks, step by step.

## 5. Documentation and matrix

- [x] 5.1 Update the README, the `rbind` note in the matrix and the volume behaviour text (design D5, D10) @worker:local
  - Priority: P0. Files: `README.md`, `tests/compat/matrix.json`, `docs/testing.md`. Depends: 1.1, 3.3.
  - Tests: the matrix note and the README paragraph on local bind volumes list `bind`, `ro`, `rw` and not `rbind`; the README describes `down` + `up` giving a fresh anonymous volume, `up -V`, and the BREAKING note; no document says the shim keeps anonymous volumes across `down`.
  - Done: `python3 scripts/render_compat_table.py --check README.md` passes after `--write`; `python3 -m unittest tests.test_docs tests.test_compat_matrix tests.test_compat_matrix_schema -v` passes.
- [x] 5.2 Add matrix rows for `compose up --renew-anon-volumes` and for the lifecycle, naming the 3.3, 3.4 and 3.5 tests @worker:local
  - Priority: P1. Files: `tests/compat/matrix.json`, `README.md` (rendered block). Depends: 3.4, 3.5, 5.1.
  - Tests: the matrix tests accept the rows; a `live` or `conformance` claim names a test that exists.
  - Done: `python3 -m unittest tests.test_compat_matrix tests.test_compat_matrix_schema -v` passes and `render_compat_table.py --check README.md` passes.

## 6. Final verification

- [x] 6.1 Run the whole suite and the hosted checks locally @worker:orchestrator
  - Priority: P0. Files: none. Depends: all.
  - Done: `python3 -m unittest discover -s tests -v`, `python3 scripts/test_report.py --tiers unit,fake`, `python3 -m unittest tests.test_compat_matrix tests.test_compat_matrix_schema -v`, `python3 scripts/render_compat_table.py --check README.md`, `python3 -m compileall -q src scripts tests` and `openspec validate close-lifecycle-and-evidence-gaps --strict` all pass.
- [x] 6.2 Run the opt-in tiers for the changed behaviour and check for leftovers @worker:orchestrator
  - Priority: P0. Files: none. Depends: 6.1.
  - Done: the fixture S live test and the conformance run with `--require conformance` pass; `container volume list`, `container list --all` and `container network list` show no `shimtest-` objects, and `/usr/local/bin/docker volume ls` shows no `conf-` ones. The pre-existing `fixture_b` live flake is not part of this change: if it fails, record that and do not fix it here.
- [x] 6.3 Confirm that no evidence file was committed and that the verifier rejects the repository as it stands for a final tag @worker:orchestrator
  - Priority: P1. Files: none. Depends: 6.1.
  - Done: `ls docs/evidence` shows nothing but an optional `README`/`.gitkeep`; `python3 scripts/verify_release_evidence.py --tag v9.9.9` exits 3 with "no evidence"; `--tag v9.9.9rc1` exits 0. Producing the first real evidence is a release-time step (`@needs-human`), recorded in `docs/releasing.md`.
