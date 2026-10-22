# Proposal

## Why

An external review of the lifecycle-and-evidence change found two real risks and two gaps. `up` now deletes an anonymous volume left by `down`, and `up -V` deletes the previous one, so the shim destroys data that Docker only orphans: a user who runs `down` and `up` loses what the old container wrote with no way back. Separately, the release evidence does not cover the packaging configuration, nothing ever installs the built wheel, and no complete live validation has been run and committed for `container 1.5.0`, so no final `vX.Y.Z` tag can pass the gate. The shim is meant to replace Docker on a developer machine, so these must close before the first release.

## What Changes

- **BREAKING (relative to the unreleased previous change):** anonymous volumes are never deleted implicitly. A new volume gets a unique name; a container that is recreated keeps the volume it already uses, found from the container's own mounts; a volume left by `down` stays as a labelled orphan. `up -V` mounts a new volume and leaves the previous one in place. Only `down -v` removes anonymous volumes, now every volume labelled for the project and marked anonymous, orphans included. The deterministic name, the "residue is deleted" rule and the "volume in use" and "unlabelled same-name volume" stops go away.
- The release evidence identity covers the packaging configuration: the combined hash also takes the `pyproject.toml` blob, the schema version moves to 2, and the verifier compares the same inputs.
- The built wheel is installed and exercised before a release: a script builds the wheel, installs it into a clean virtual environment, checks the `docker` entry point, the version and the declared Python floor, and the `checks` job of `release.yml` and the CI run it. The `release` job publishes the wheel that was tested.
- The full live tier is made reliable on `container 1.5.0` (the `fixture_b` flake is diagnosed and fixed), and a complete validation is run on the maintainer machine, producing the first file under `docs/evidence/`.
- README, `docs/testing.md`, `docs/releasing.md`, the compatibility matrix and the tests are updated to the new volume behaviour and to the new evidence and packaging checks.

Out of scope: running the shim on a macOS version other than 26, signing the evidence, a self-hosted runner, a new `container` release, and automatic cleanup or a pruning command for orphaned volumes (the user removes them with `down -v` or `docker volume rm`).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `compose-anonymous-volumes`: unique names, reuse through the container's mounts, no implicit deletion, `-V` leaves the old volume, `down -v` removes all labelled anonymous volumes of the project.
- `release-evidence`: the tree hash includes the packaging configuration, the schema is version 2, and a complete live validation is required to write the file.
- `fork-distribution`: the built wheel is installed in a clean environment and exercised before a release, and the tested wheel is the one published.
- `test-tiers`: the live tier completes on the supported runtime without flaky outcomes in the fixtures it declares.

## Impact

- Code: `src/container_docker_shim/compose.py` (anonymous volume planning, `up`, `down -v`), `compose_resources.py` (volume identity), `scripts/validate-runtime.py` and `scripts/verify_release_evidence.py` (identity), a new `scripts/verify_wheel.py`, `.github/workflows/ci.yml` and `release.yml`, `pyproject.toml` only if the Python floor test requires it.
- Tests: `tests/test_compose.py`, `tests/test_live_fixtures.py`, `tests/test_conformance_fixtures.py` (fixture S asserts orphans survive and `down -v` removes them), `tests/test_validate_runtime.py`, `tests/test_verify_release_evidence.py`, `tests/test_workflows.py`, `tests/test_docs.py`, new tests for the wheel script.
- Docs and data: `README.md`, `docs/testing.md`, `docs/releasing.md`, `tests/compat/matrix.json`, and the first evidence file under `docs/evidence/`.
- Runtime: still only `container 1.5.0` on macOS 26. No version bump, no new dependency (the wheel script uses the standard library and `python -m build` and `venv` that the release job already uses).
