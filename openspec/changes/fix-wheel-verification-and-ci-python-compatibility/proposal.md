## Why

The CI run for `bd19186` (https://github.com/fparrav/docker-for-apple-container/actions/runs/39797891319) failed both `Wheel` jobs, and both failures also sit in `release.yml`, so the first tag pushed today could not be published:

1. **`Wheel (Python 3.x)`**: `scripts/verify_wheel.py --dist dist` returns exit 3. `find_wheel()` keeps the relative path `dist/<wheel>`; `install()` then runs `pip` with `cwd` set to a fresh temporary directory, so `pip` looks for `/tmp/verify-wheel-*/dist/<wheel>` and reports `ERROR: Could not install packages due to an OSError: [Errno 2] No such file or directory`. Reproduced locally on Python 3.12 and 3.9.6: relative `--dist` fails, absolute `--dist` passes. The unit tests never saw it because every test passes an absolute `tempfile` directory.
2. **`Wheel (Python 3.9)`**: the job stops at `Set up Python` with `The version '3.9' with architecture 'x64' was not found for Ubuntu 26.04`. `ubuntu-latest` began migrating to Ubuntu 26.04 on 2026-10-19 (the run's annotation says so). The `actions/python-versions` manifest has CPython 3.9.25 for Ubuntu 22.04 and 24.04 and none for 26.04 (3.10.22 does have 26.04). The `smoke` matrix in `release.yml` has the same `ubuntu-latest` + `"3.9"` pair.

Both defects defeat one goal: the artifact that is distributed must be installable and runnable, and a failure must stop the release. Today the check that guards that goal fails for reasons that have nothing to do with the artifact, so it cannot be trusted either way.

## What Changes

- `scripts/verify_wheel.py` resolves the wheel to an absolute path once, in the single place that finds it, so the install does not depend on the working directory of the caller or of the `pip` subprocess. Relative and absolute `--dist` values, paths with spaces and calls from another directory work. Failure messages name the full path of the wheel.
- The verifier prints one extra `ok` line naming the interpreter and platform it ran on, so each CI and smoke log states exactly what was checked.
- The `wheel` job in `ci.yml` and the `smoke` job in `release.yml` stop using `ubuntu-latest` for the Python floor: the matrix becomes an explicit list of legs, the floor (`3.9`) on `ubuntu-24.04` and the newest release (`3.x`) on `ubuntu-latest`. The two matrices must stay identical.
- Regression tests: the verifier gets the eight scenarios of the report (relative and absolute `dist/`, a directory with spaces, no wheel, two wheels, a clean install, an install from another working directory, cleanup after a failed install); the workflow tests learn the leg-based matrix and gain guards that keep the floor leg on a pinned runner image and keep release publication blocked by every smoke leg.
- `docs/testing.md` and `docs/releasing.md` state which interpreter and runner image check the wheel, why the floor leg is pinned, how to maintain the pin, and that touching `scripts/` or `tests/` invalidates the committed live evidence.

Not changed: the Python floor stays `>=3.9` (the 87 wheel, workflow and evidence tests pass on 3.9.6 today, and the 3.9 leg proves it again on a hosted runner); third-party actions keep their SHAs; permissions stay as they are; no new runner, action or dependency.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `fork-distribution`: new requirements for wheel-path independence, isolated and cleaned verification, accurate failure and environment reporting, and a release that publishes only the artifact every smoke leg installed.
- `ci-evidence`: new requirement that the jobs checking the Python floor run on a pinned runner image with verified interpreter availability, alongside a modern leg on the moving label, and that the two workflows keep the same legs.
- `release-evidence`: new requirement that the evidence stays bound to the code that was live-validated, that changes to the packaging verifier or its tests invalidate it for the next final tag, and that packaging checks are never recorded as live evidence.

All three use ADDED requirements only. `harden-volume-renewal-and-release-evidence` is complete (18/18) but not archived, and its deltas for `fork-distribution` and `release-evidence` are not in the main specs yet; adding rather than modifying keeps this change independent of the archive order.

## Impact

- Code: `scripts/verify_wheel.py` (path resolution, messages, environment line). No file under `src/`.
- Tests: `tests/test_verify_wheel.py`, `tests/test_workflows.py`, `tests/test_docs.py`.
- Workflows: `.github/workflows/ci.yml` (`wheel` job), `.github/workflows/release.yml` (`smoke` job).
- Docs: `docs/testing.md`, `docs/releasing.md`.
- Release evidence: `docs/evidence/runtime-1.5.0-20261022T125604Z.json` covers the current `scripts/` and `tests/` trees. After this change lands, `scripts/verify_release_evidence.py` rejects it for a final `vX.Y.Z` tag until `scripts/validate-runtime.py --evidence` is run again on a clean tree on macOS 26 with `container 1.5.0`. The existing file is neither edited nor deleted; the newer file supersedes it. Pre-release tags (`rc`, `a`, `b`, `dev`) are unaffected.
- Out of scope: Docker Compose compatibility, runtime behavior, raising or dropping the Python floor, attaching the sdist to the smoke test, changing how the evidence hash is computed, self-hosted runners.
