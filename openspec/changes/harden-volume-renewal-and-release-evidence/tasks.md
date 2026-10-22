# Tasks

Conventions for every task:

- Python 3.9 standard library only, `from __future__ import annotations`, unittest only. Tests are top-level modules in `tests/` with a module-level `TIER`; live tests are opt-in (`SHIM_TIERS=live`), name objects `shimtest-<topic>-<short uuid>`, clean up by exact name, never prune. Run `python3 -m unittest discover -s tests -v` before closing a group.
- Each task lists `Priority` (P0 must ship, P1 should ship), `Files`, `Depends` and `Done`. `compose.py` is a hotspot: every task that edits it is chained through `Depends`, so no two run at once. The same holds for `scripts/validate-runtime.py`, `scripts/verify_release_evidence.py` and the two workflow files.
- Matrix entries (`tests/compat/matrix.json`) change in group 5; regenerate the README block with `python3 scripts/render_compat_table.py --write README.md`. A matrix row may claim `live` or `conformance` only when a test in this change proves it.
- The supported runtime stays `container 1.5.0`; do not touch `SUPPORTED_CONTAINER_VERSION`. Do not stage `openspec/` or `AGENTS.md`.
- Real Docker for the conformance tier is `/usr/local/bin/docker` (plain `docker` is the shim): `SHIM_TIERS=conformance SHIM_CONFORMANCE_DOCKER=/usr/local/bin/docker`. Live tests that create objects remove them by exact name, and a task that runs the live tier checks `container volume list`, `container list --all` and `container network list` for leftovers afterwards.
- Anonymous volumes created by the live and fake tests carry the project label of the test project; a task that deletes anything in the runtime names the exact object and never prunes.
- Evidence files are produced only by task 6.2. No other task commits a file under `docs/evidence/` except fixtures built in a temporary directory.
- Do not commit, push or tag from a task unless the task says so.

## 1. Anonymous volumes never deleted implicitly

- [x] 1.1 Add `new_anonymous_volume` (unique name from an injectable token) and `anonymous_labels`, keeping the old `anonymous_volume` until 1.2 and 1.4 stop using it (design D1) @worker:local
  - Priority: P0. Files: `src/container_docker_shim/compose_resources.py`, `tests/test_compose_resources.py`. Depends: none.
  - Tests: two calls to `new_anonymous_volume` with the default source give different names that start with `<project>_<service>_anon-` followed by 10 hex characters; a replaced `token` gives the predicted name; the labels hold project, service and the target in `.anonymous` and equal `anonymous_labels(...)`; the name does not depend on the target; the existing `anonymous_volume` and `_resolve_anonymous` stay untouched so the rest of the suite keeps passing.
  - Done: the module tests pass without filesystem or runtime access.
- [x] 1.2 Reuse the volume the existing container mounts, create new volumes otherwise, and delete the discard path (design D2, D3) @worker:orchestrator
  - Priority: P0. Files: `src/container_docker_shim/compose.py`. Depends: 1.1.
  - Behaviour: `compose.py` calls `new_anonymous_volume` and no longer calls `anonymous_volume` (its definition, `_resolve_anonymous` that still calls it, and their tests are deleted in 1.4); `recreate` without `-V` mounts, per anonymous target, the `volume` mount of the existing container at that destination when the volume still exists, otherwise a new seeded volume; `create` and `-V` always create new volumes; `_discard_anonymous_volume` and the in-use and unlabelled stops are removed; a name collision retries up to three times; a failed seed removes only the volume just created and exits 1 naming the service, image and target; `--dry-run` creates nothing.
  - Tests: covered by 1.3. Done: `python3 -m unittest tests.test_compose -v` runs; the failures that remain are exactly the tests 1.3 rewrites.
- [x] 1.3 Rewrite the fake-runtime tests for the new lifecycle @worker:orchestrator
  - Priority: P0. Files: `tests/test_compose.py`. Depends: 1.2.
  - Tests: `down` then `up` creates a second volume and leaves the first; container removed outside Compose does the same; `--force-recreate` mounts the volume of the existing container, found from its mounts and not by name; a container whose volume has an old deterministic-style name keeps it; a new target gets a new volume and the others are reused; `-V` with `--force-recreate` mounts new volumes and removes nothing; a kept or only-started container is untouched; a taken name retries and never reuses; a failed seed removes only the new volume; `--dry-run` runs no `volume create`, `volume rm` or `run`; nothing in the suite calls `volume rm` on an anonymous volume outside `down -v`.
  - Done: `python3 -m unittest tests.test_compose -v` passes. The coordinator greps the fake call logs of the new tests for `volume rm` and sees none.
- [x] 1.4 `down -v` removes every labelled anonymous volume of the project, orphans included (design D4) @worker:orchestrator
  - Priority: P0. Files: `src/container_docker_shim/compose.py`, `tests/test_compose.py`. Depends: 1.3.
  - Also: delete `_resolve_anonymous`, `Resources.anonymous` and their uses (`_runtime_volumes` no longer lists anonymous volumes). Tests: three volumes labelled for the project (current, orphan from an earlier `down`, one of a removed service) are all removed; a volume with another project label, an unlabelled one and an external named volume are not; a refusal on one volume does not stop the others and ends with exit 1 naming it; an unreadable file still exits 64 and removes nothing; plain `down` removes no volume.
  - Done: `python3 -m unittest tests.test_compose tests.test_compose_resource_compat -v` passes.
- [x] 1.5 Update live and conformance fixture S to the new contract (design D5) @worker:orchestrator
  - Priority: P0. Files: `tests/test_live_fixtures.py`, `tests/test_conformance_fixtures.py`. Depends: 1.4.
  - Tests: sequence marker, `up --force-recreate` keeps it, `down`, `up` shows it gone with the image content present and the first run's volume (named from the container's mounts before `down`) still existing, `up -V --force-recreate` gives another volume, `down -v` removes the volume of the current container. Only the live test also asserts that the orphans are gone after `down -v`; the conformance test does not.
  - Done: the coordinator runs `SHIM_TIERS=live python3 -m unittest tests.test_live_fixtures -k anonymous -v` on container 1.5.0 and `SHIM_TIERS=conformance SHIM_CONFORMANCE_DOCKER=/usr/local/bin/docker python3 -m unittest tests.test_conformance_fixtures -k anonymous -v`, both pass, and the three leftover listings are empty of `shimtest-`/`conf-` objects.

## 2. Evidence identity covers packaging

- [x] 2.1 `validate-runtime.py` hashes `pyproject.toml` with the trees, writes schema 2 and refuses a dirty `pyproject.toml` (design D6) @worker:local
  - Priority: P1. Files: `scripts/validate-runtime.py`, `tests/test_validate_runtime.py`. Depends: none.
  - Tests: the hash text has four lines in the order `src`, `scripts`, `tests`, `pyproject.toml`; changing only the `pyproject.toml` id changes the hash; changing only `README.md` does not; a diff in `pyproject.toml` makes `--evidence` refuse naming it; the file records schema 2 and the four ids under `trees`.
  - Done: `python3 -m unittest tests.test_validate_runtime -v` passes.
- [x] 2.2 `verify_release_evidence.py` recomputes the same hash, accepts only schema 2 and names both numbers otherwise (design D6) @worker:local
  - Priority: P1. Files: `scripts/verify_release_evidence.py`, `tests/test_verify_release_evidence.py`. Depends: 2.1.
  - Tests: a schema 1 file fails with a message naming 1 and 2 and saying to run the validation again; a packaging change after the evidence fails naming both hashes; a documentation-only commit keeps it valid; the shape check requires the four ids; a unit test asserts both scripts use the same `EVIDENCE_TREES`, `EVIDENCE_FILES` and hash text (import the two modules).
  - Done: `python3 -m unittest tests.test_verify_release_evidence tests.test_validate_runtime -v` passes.

## 3. Wheel install check

- [x] 3.1 Add `scripts/verify_wheel.py` and its tests (design D7) @worker:orchestrator
  - Priority: P1. Files: `scripts/verify_wheel.py` (new), `tests/test_verify_wheel.py` (new, `TIER = "unit"`). Depends: none.
  - Tests: pure checks on a synthetic wheel built with `zipfile` in a temporary directory (missing `cli.py`, missing or wrong entry point, two wheels, no wheel); the venv steps run against a tiny real package wheel built in the test only when `python -m build` is importable, otherwise they are skipped with that reason; the stand-in `container` script is written to a temporary directory and removed; the script exits 3 on the first failed check.
  - Done: `python3 -m unittest tests.test_verify_wheel -v` passes; the coordinator runs `python3 -m build --outdir <scratch>/dist` and `python3 scripts/verify_wheel.py --dist <scratch>/dist` and sees every check pass, then deletes the build output.
- [x] 3.2 Run the unit suite and the wheel check on Python 3.9 and record the outcome (design Risks) @worker:orchestrator
  - Priority: P1. Files: `pyproject.toml` only if the floor must change, `docs/releasing.md` if it does. Depends: 3.1.
  - Done: the coordinator runs `/usr/bin/python3 -m unittest discover -s tests` (3.9.6) and `scripts/verify_wheel.py` under it and reports the result. If 3.9 fails because of the code, the task raises `requires-python` to the lowest passing version and updates the fork-distribution scenario; it does not change the code to suit it.
- [x] 3.3 Add the `wheel` job to `ci.yml` and assert it in the workflow tests (design D8) @worker:local
  - Priority: P1. Files: `.github/workflows/ci.yml`, `tests/test_workflows.py`. Depends: 3.2.
  - Tests: the job is read-only, checks out with `fetch-depth: 0`, has a matrix with the floor from `pyproject.toml` and `3.x`, builds with `python -m build` and runs `verify_wheel.py`; no step selects a self-hosted runner; actions are pinned to SHAs.
  - Done: `python3 -m unittest tests.test_workflows -v` passes.
- [x] 3.4 Restructure `release.yml`: `package`, `smoke` and `release` use one set of files (design D8) @worker:orchestrator
  - Priority: P1. Files: `.github/workflows/release.yml`, `tests/test_workflows.py`. Depends: 3.3, 2.2.
  - Steps: look up the full commit SHA and release of the upload and download artifact actions with `gh api` and record them in a comment; `package` builds and uploads `dist/`; `smoke` (matrix) downloads it and runs `verify_wheel.py --expect-version` with the tag minus `v`, the tag passed through `env`; `release` needs `checks` and `smoke`, downloads `dist/`, does not build, and keeps `contents: write` only there.
  - Tests: `check_evidence_gate` still passes; new checks assert the `release` job has no `python -m build` or `pip install build`, downloads the artifact, depends on `smoke`, the tag is not in any `run:` line, and `package` and `smoke` have no `contents: write`.
  - Done: `python3 -m unittest tests.test_workflows -v` passes and the coordinator reads the final workflow file in full.

## 4. Live tier reliability

- [x] 4.1 Reproduce the `fixture_b` live failure and record its cause (design D9) @worker:orchestrator
  - Priority: P0. Files: none changed. Depends: none.
  - Done: the coordinator runs `SHIM_TIERS=live python3 -m unittest tests.test_live_fixtures -k fixture_b -v` five times, captures the container state and logs at each failure, and writes the finding (failing step, timing, whether it is the test or the runtime) in the task's note. If the cause is a runtime bug, the work stops and the user is told.
  - Finding: the failure did not reproduce. `fixture_b` passed five times in a row alone and once inside a complete live run of 40 tests on a clean copy of HEAD (container 1.5.0, no failure, no leftover object), so no container state or log of a failure was captured. Cause not proven; the likely one was found by reading the code and the image: the postgres entrypoint starts a temporary server with `listen_addresses=''` (unix socket only) while it initialises the data directory and then stops it, so `pg_isready` over the socket could succeed against that server and the next `psql` land in the restart. It is a test race, not a runtime bug. 4.2 asks readiness of the TCP listener (`pg_isready -h 127.0.0.1`), which only the real server opens.
- [x] 4.2 Add `wait_until` and use it where a verdict depends on readiness (design D9) @worker:orchestrator
  - Priority: P0. Files: `tests/` helper module (new, unit-tested), `tests/test_live_fixtures.py`. Depends: 4.1.
  - Tests: `wait_until` returns when the check is true, retries until the timeout, and on timeout fails with the description and the last state; a fake clock keeps the unit test instant; `grep -n "time.sleep" tests/test_live_fixtures.py` lists none that decides a verdict.
  - Done: `python3 -m unittest` for the helper passes and the fixed test passes five times in a row.
- [x] 4.3 Run the complete live tier three times in a row on container 1.5.0 @worker:orchestrator
  - Priority: P0. Files: none. Depends: 4.2, 1.5.
  - Done: the coordinator runs `SHIM_TIERS=live python3 scripts/test_report.py --tiers live --require live` three times in the background, between runs lists `container volume list`, `container list --all` and `container network list` and sees no `shimtest-` object; all three report every declared test passed.
  - Result: three consecutive runs on container 1.5.0 (00:41, 00:50, 00:58) each exited 0 with live 40 ran, 40 passed, 0 failed, 0 skipped, 0 not run; after each run the lists held only `buildkit` and the unrelated `hermes-data` volume, no `shimtest-` or `_anon-` object.

## 5. Documentation and matrix

- [x] 5.1 README, matrix and regenerated block for the new volume behaviour (design D11) @worker:local
  - Priority: P1. Files: `README.md`, `tests/compat/matrix.json`. Depends: 1.5.
  - Tests: no sentence says the shim deletes or reseeds leftover volumes on `up`; the README explains orphans, `up -V` and `down -v` and shows `docker volume ls --filter label=com.docker.compose.project=<p>`; the **Breaking change** note is replaced by one that says anonymous volumes are never deleted implicitly; the anonymous-volume and `--renew-anon-volumes` rows describe the new behaviour.
  - Done: `python3 scripts/render_compat_table.py --check README.md` and `python3 -m unittest tests.test_compat_matrix tests.test_compat_matrix_schema -v` pass.
- [x] 5.2 `docs/testing.md`, `docs/releasing.md` and the documentation tests @worker:local
  - Priority: P1. Files: `docs/testing.md`, `docs/releasing.md`, `tests/test_docs.py`. Depends: 3.4, 4.2, 2.2.
  - Tests: `VolumeLifecycleDocsTests` asserts the new wording; the release docs mention `verify_wheel.py`, schema 2, `pyproject.toml` in the hash and that the published wheel is the tested one; `testing.md` mentions `wait_until`.
  - Done: `python3 -m unittest tests.test_docs -v` passes.

## 6. Verification and first evidence

- [ ] 6.1 Full verification before closing the change @worker:orchestrator
  - Priority: P0. Depends: all of groups 1 to 5.
  - Done: `python3 -m unittest discover -s tests`, `python3 scripts/test_report.py --tiers unit,fake`, `python3 -m compileall -q src scripts tests`, `openspec validate harden-volume-renewal-and-release-evidence --strict` all pass; `SHIM_TIERS=conformance SHIM_CONFORMANCE_DOCKER=/usr/local/bin/docker python3 scripts/test_report.py --require conformance` exits 0; no leftover object in Apple `container` or Docker.
- [ ] 6.2 Produce and commit the first evidence file (design D10) @needs-human
  - Priority: P0. Files: `docs/evidence/runtime-1.5.0-<timestamp>.json` (new). Depends: 6.1 and the user having committed and merged the code, so the working tree is clean.
  - Why a person: it needs the maintainer's macOS 26 machine, the committed code and the decision to commit the file; pushing needs the VPN off.
  - Done: `python3 scripts/validate-runtime.py --evidence` exits 0 and writes one file; `python3 scripts/verify_release_evidence.py --tag v9.9.9` exits 0 (the final-tag form against the current commit); the file is committed on its own with a `docs` scope message.
