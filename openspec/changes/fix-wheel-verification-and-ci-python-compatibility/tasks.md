# Tasks

Conventions for every task:

- Python 3.9 standard library only, `from __future__ import annotations`, unittest only. Tests are top-level modules in `tests/` with a module-level `TIER`. Run `python3 -m unittest discover -s tests -v` before closing a group, and the group's own modules under `/usr/bin/python3` (3.9.6) too.
- Each task lists `Priority` (P0 must ship, P1 should ship), `Files`, `Depends` and `Done`. Files shared between tasks are chained through `Depends`, so no two workers edit the same file at once: `scripts/verify_wheel.py` and `tests/test_verify_wheel.py` (group 1), `.github/workflows/ci.yml`, `.github/workflows/release.yml` and `tests/test_workflows.py` (group 2), `docs/*.md` and `tests/test_docs.py` (group 3). Groups 1 and 2 touch disjoint files and may run in parallel.
- Only the two floor/modern legs, path resolution and their guards are in scope. Do not touch `src/`, `pyproject.toml`, `SUPPORTED_CONTAINER_VERSION`, the compatibility matrix, or any file under `docs/evidence/`. Do not edit, delete, copy or regenerate an evidence file by hand.
- Third-party actions keep their SHAs; do not add an action, a runner, a permission or a dependency.
- Use scratch directories for every build or wheel (`--outdir`/temporary directory) and delete them afterwards; never leave `dist/` or `build/` in the checkout.
- Do not commit, push or tag from a task unless the task says so. Do not stage `openspec/` or `AGENTS.md`.
- Pushing over SSH needs the VPN off; pushing and tagging need the user's go-ahead.

## 1. Wheel path resolution (design D1, D2)

- [x] 1.1 Add the regression tests for the verifier, first failing for the right reason @route:claude-sonnet/medium
  - Priority: P0. Files: `tests/test_verify_wheel.py`. Depends: none.
  - Tests (each builds the wheel with the existing `write_wheel` helper; relative cases use `os.chdir` inside `try/finally` or `mock.patch` of the cwd, restoring it):
    1. `find_wheel` returns an absolute path for a relative `dist`, and the same file for the absolute form;
    2. `verify` passes end to end with a relative `dist` while the current directory is the wheel's parent, and the output has the six existing `ok` lines;
    3. same with a directory whose name contains spaces;
    4. same from a current directory that is not the wheel's parent: the test changes to another temporary directory and passes the path computed with `os.path.relpath` from there (for example `../other/dist`);
    5. `main(["--dist", "<relative>"])` returns 0;
    6. a missing wheel and a missing directory exit 3 with one stderr line naming the directory as given;
    7. two wheels exit 3 naming both and `install` is not called (`mock.patch.object`);
    8. a failed install (patch `run` to return a non-zero `pip`) exits 3, the stderr line contains the absolute wheel path, and no `verify-wheel-*` directory is left (compare the temp directory before and after);
    9. a failed later check (patch `check_runtime_absent` to raise) also leaves no `verify-wheel-*` directory;
    10. the `pip` argument list contains `--no-index`, `--no-deps` and the absolute wheel path (patch `run` and capture the argv).
  - Done: `python3 -m unittest tests.test_verify_wheel -v` runs; with the unfixed `scripts/verify_wheel.py` exactly the relative-path tests (2, 3, 4, 5, and the absolute-path assertions of 1 and 10) fail, and the rest pass. Record the failing test names in the task result.
- [x] 1.2 Resolve the wheel once in `find_wheel` (design D1) @route:pi-qwen/xhigh
  - Priority: P0. Files: `scripts/verify_wheel.py`. Depends: 1.1.
  - Change: `find_wheel` returns `wheels[0].resolve()` and its docstring says so; no other function resolves paths; `verify`, `check_contents` and `install` keep their signatures.
  - Done: `python3 -m unittest tests.test_verify_wheel -v` passes, also under `/usr/bin/python3`. Reproduction: build a wheel into a scratch `dist/`, `cd` to the scratch directory and run `python3 <repo>/scripts/verify_wheel.py --dist dist` (exit 0), then the absolute form (exit 0) and from `/` with a relative path computed to it (exit 0). `git diff --stat` shows one function changed in the script.
- [x] 1.3 Name the file in install errors and print the environment line (design D1, D2) @route:pi-qwen/xhigh
  - Priority: P1. Files: `scripts/verify_wheel.py`, `tests/test_verify_wheel.py`. Depends: 1.2.
  - Change: `install` reports `pip could not install <wheel path>: <tail>`; `verify` adds `ok environment: Python <version> on <platform>` after the contents line and before the install; the module docstring lists the new line. The existing test that counts six `ok` lines becomes seven.
  - Tests: the failing-install test from 1.1 asserts the absolute path; a new test asserts the environment line contains `platform.python_version()`; a test asserts the script writes nothing under `docs/evidence/` and does not import `verify_release_evidence` (read the source text).
  - Done: `python3 -m unittest tests.test_verify_wheel -v` passes on 3.12 and under `/usr/bin/python3`; the full suite passes.

## 2. Matrix legs and release pipeline guards (design D3, D4, D5)

- [x] 2.1 Rewrite the workflow test helpers for leg-based matrices and update `ci.yml` @route:claude-sonnet/medium
  - Priority: P0. Files: `tests/test_workflows.py`, `.github/workflows/ci.yml`. Depends: none.
  - Change in the test module: replace `matrix_pythons` with `matrix_legs(job)` returning `[{"python": ..., "os": ...}]` parsed from `include:` entries; `check_wheel_job` asserts the Pythons are exactly the floor and `3.x`, the floor leg's `os` does not end in `-latest`, the `3.x` leg's `os` is `ubuntu-latest`, `runs-on: ${{ matrix.os }}`, `python-version: ${{ matrix.python }}`, `matrix.os` and `matrix.python` absent from `run:` lines. Update `test_a_missing_python_floor_is_flagged` to the new shape.
  - Change in `ci.yml`: the `wheel` job uses `runs-on: ${{ matrix.os }}` and the two-leg `include:` matrix of design D3, with a comment naming the reason for the pin; everything else in the job (full-history checkout, `python3 -m build`, `python3 scripts/verify_wheel.py --dist dist`) stays as is.
  - Tests (synthetic, replacing text in the real file): floor leg removed; floor leg on `ubuntu-latest`; a literal `runs-on: ubuntu-latest`; `3.x` leg on a versioned label; `${{ matrix.os }}` in a `run:` line; each is flagged.
  - Done: `python3 -m unittest tests.test_workflows -v` passes; `git diff` of `ci.yml` shows only the `wheel` job's `runs-on`, `matrix` and comment lines.
- [x] 2.2 Apply the same legs to the `smoke` job of `release.yml` and compare both files @route:claude-sonnet/medium
  - Priority: P0. Files: `.github/workflows/release.yml`, `tests/test_workflows.py`. Depends: 2.1.
  - Change in `release.yml`: `smoke` uses `runs-on: ${{ matrix.os }}` and the same `include:` matrix; update the header comment ("installs that wheel ... on the Python floor and on the newest release") to name the pinned image. `check_release_packaging` uses `matrix_legs` with the same rules as 2.1; a new test asserts the legs of the `wheel` job and the `smoke` job are equal.
  - Tests: legs differing between the files are flagged; the existing release tests (`release builds the files again`, `does not depend on smoke`, tag outside env) still pass.
  - Done: `python3 -m unittest tests.test_workflows -v` passes; `git diff` of `release.yml` is limited to the `smoke` job and the header comment; only `release` holds `contents: write`.
- [x] 2.3 Add the release-integrity guards (design D5) @route:claude-sonnet/medium
  - Priority: P1. Files: `tests/test_workflows.py`; `.github/workflows/release.yml` only if a guard finds a real gap. Depends: 2.2.
  - Tests: the artifact `name:` uploaded in `package` equals the one downloaded in `smoke` and in `release`; `smoke` has `needs: package`; neither `smoke` nor `release` has `continue-on-error`; `release` has no `if:` line; each rule has a synthetic-text test that flags a violation (rename the artifact in one job, add `continue-on-error: true` to `smoke`, add `if: always()` to `release`).
  - Done: `python3 -m unittest tests.test_workflows -v` passes. If a guard fails on the real file, the task fixes the workflow with the smallest change and reports it.
- [x] 2.4 Close the two review findings in the workflow guards (review of 2.2 and 2.3) @route:pi-qwen/xhigh
  - Priority: P1. Files: `tests/test_workflows.py` only. Depends: 2.3.
  - Finding 1 (2.2): `test_smoke_legs_differing_from_the_wheel_legs_are_flagged` only asserts `assertNotEqual` on two `matrix_legs` results, so no guard is exercised. Extract the comparison of the `ci.yml` wheel legs with the `release.yml` smoke legs into a function that returns violations, use it in `test_the_smoke_legs_equal_the_wheel_legs` (expects none) and in the synthetic test (a smoke leg moved to `ubuntu-22.04` must be reported by that function).
  - Finding 2 (2.3): `check_release_packaging` looks for `if:` only in `release`, but the spec (`fork-distribution`, "or to be skipped by a condition") also forbids it for `smoke`. Make the guard flag a job-level `if:` in `smoke` as well as in `release`, add a synthetic test that inserts `if: false` into `smoke` and expects a violation, and keep the real `release.yml` passing (it has no `if:` today).
  - Done: `python3 -m unittest tests.test_workflows -v` passes on `python3` and `/usr/bin/python3`; the full suite passes.

## 3. Documentation (design D6, D7, D8)

- [x] 3.1 Document the legs, the pin and the evidence consequence, and pin the documentation to the workflows @route:claude-sonnet/medium
  - Priority: P1. Files: `docs/releasing.md`, `docs/testing.md`, `tests/test_docs.py`. Depends: 1.3, 2.2.
  - Change: `docs/releasing.md`: the statement about runners is accurate for the `smoke` job; add a table of the two legs (3.9 on `ubuntu-24.04`, `3.x` on `ubuntu-latest`) and why the floor is pinned; the maintenance procedure of D7 with the manifest command; the verifier prints the interpreter and platform; the Release evidence section lists that a change to `scripts/` or `tests/` (this one included) requires `validate-runtime.py --evidence` again, that the previous file stays as history, and that packaging checks are not live evidence. `docs/testing.md` "The wheel check": `--dist` may be relative or absolute, CI runs `--dist dist`, the tests cover both.
  - Tests: the documented floor leg (version and runner) equals the one in `ci.yml` and `release.yml`; both documents mention that `scripts/` and `tests/` changes invalidate the evidence; no statement in the documents says the wheel check is live evidence.
  - Done: `python3 -m unittest tests.test_docs -v` passes.

## 4. Verification and consequences

- [x] 4.1 Full local verification @worker:orchestrator
  - Priority: P0. Files: none. Depends: 1.3, 2.3, 3.1.
  - Done: `python3 -m unittest discover -s tests` and `/usr/bin/python3 -m unittest discover -s tests` (3.9.6) pass; `python3 scripts/test_report.py --tiers unit,fake` exits 0; `python3 -m compileall -q src scripts tests` and `git diff --check` are clean; `python3 scripts/render_compat_table.py --check README.md` passes; `openspec validate fix-wheel-verification-and-ci-python-compatibility --strict` passes. In a scratch directory: `python3 -m build --outdir <scratch>/dist`, then from `<scratch>` `python3 <repo>/scripts/verify_wheel.py --dist dist --expect-version <version printed by the build>` passes, and the absolute form passes, under 3.12 and `/usr/bin/python3`. `git status` shows no `dist/` or `build/` and no change under `docs/evidence/`. Report the results with command output counts.
- [x] 4.2 Check the evidence impact without writing any evidence @worker:orchestrator
  - Priority: P0. Files: none. Depends: 4.1 and the code committed by the user on a branch.
  - Done: on the committed branch, `python3 scripts/verify_release_evidence.py --tag v9.9.9` exits non-zero naming the recorded hash `3276a3bc74b6` and the current one (expected: `scripts/` and `tests/` changed), and `python3 scripts/verify_release_evidence.py --tag v9.9.9rc1` exits 0 as a pre-release. A second check on a throwaway commit that changes only `docs/releasing.md` and `.github/workflows/ci.yml` shows the hash unchanged (use `git stash`/a scratch worktree, delete it afterwards). Report both outcomes.
- [ ] 4.3 First hosted run of both legs @needs-human
  - Priority: P0. Files: none. Depends: 4.2 and the user's go-ahead to push (VPN off for SSH).
  - Why a person: it needs a push to GitHub.
  - Done: the CI run for the pushed commit shows `Wheel (Python 3.9)` on `ubuntu-24.04` and `Wheel (Python 3.x)` on `ubuntu-latest` both green, with the `ok environment` line naming Python 3.9.x and the 3.x release respectively; `Hosted checks` and `Conformance` remain green. If the 3.9 leg fails for a code reason, stop and report: the response is a separate change that raises `requires-python`, not a workaround here.
- [ ] 4.4 Exercise the release pipeline with a pre-release tag (optional) @needs-human
  - Priority: P1. Files: none. Depends: 4.3 and the user's go-ahead (a tag push publishes a GitHub pre-release; it is outward-facing and not undone by deleting the tag alone).
  - Done: a `vX.Y.ZrcN` tag runs `checks`, `package`, both `smoke` legs and `release`, and the pre-release carries the wheel that the `smoke` legs installed.
- [ ] 4.5 Produce new live evidence before the first final tag @needs-human
  - Priority: P0 for a final release, not needed for pre-releases. Files: `docs/evidence/runtime-1.5.0-<timestamp>.json` (new). Depends: 4.3 and the change merged so the working tree is clean.
  - Why a person: it needs the maintainer's macOS 26 machine with `container 1.5.0` running, and the decision to commit the file.
  - Done: `git status --porcelain -- src scripts tests pyproject.toml` prints nothing, `python3 scripts/validate-runtime.py --evidence` exits 0 and writes one new file (40 declared, all passed), `python3 scripts/verify_release_evidence.py --tag v9.9.9` exits 0, and the file is committed on its own with a `docs/evidence` scope. The previous file stays in the repository unchanged.
