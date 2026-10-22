# Design

## Context

- Anonymous volumes are named `<project>_<service>_anon-<10hex>`, where the hex is a hash of the target (`compose_resources.anonymous_volume`), and carry the labels `com.docker.compose.project`, `.service` and `.anonymous`. `cmd_up` decides `keep`, `start`, `recreate` or `create` per service; `_emit_planned` calls `_ensure_anonymous_volume`, and the previous change made `create` and `up -V` delete an existing volume of that name through `_discard_anonymous_volume`. `down -v` removes the volumes returned by `_runtime_volumes`, which come from the services in the file. See `proposal.md` for why that destroys data Docker only orphans.
- A container row already carries its mounts: `_normalize_mounts` in `cli.py` reads `configuration.mounts`, and `_mount_type` extracts the volume name from `{"volume": {"name": ...}}`. `_project_containers` returns these rows, so the shim can see which volume a container uses without storing anything of its own. Apple runtime state stays authoritative (`AGENTS.md`).
- Apple `container` 1.5.0 refuses to remove a volume that a container uses, stopped or not.
- Evidence (`scripts/validate-runtime.py`, `scripts/verify_release_evidence.py`) hashes the git trees of `src/`, `scripts/` and `tests/` (`EVIDENCE_TREES`), duplicated in both scripts on purpose, schema 1. `pyproject.toml` is not covered.
- `release.yml` builds the sdist and wheel in the `release` job (`contents: write`) and never installs them. `ci.yml` has no build step. `requires-python = ">=3.9"` and CI runs `python-version: "3.x"` only.
- With no `container` binary the shim exits 1 with `Apple container CLI not found: set CONTAINER_DOCKER_SHIM_CONTAINER or add container to PATH` (observed). `CONTAINER_DOCKER_SHIM_CONTAINER` selects the binary.
- No tag exists in the repository, so nothing released depends on the volume behaviour of the previous change.

## Goals / Non-Goals

**Goals:**
- The shim never removes an anonymous volume unless the user runs `down -v`, and otherwise behaves like Docker for create, recreate and `-V`.
- Evidence and packaging are checked against the files that ship.
- A complete live validation passes on `container 1.5.0` and its evidence file is committed.

**Non-Goals:**
- A command to list or prune orphaned volumes, or a warning for them (the user has `volume ls --filter label=com.docker.compose.project=<p>`, `down -v` and `volume rm`).
- Signing the evidence, covering `README.md` or `LICENSE` in the hash, or building wheels for other platforms (the package is pure Python).
- Changing `SUPPORTED_CONTAINER_VERSION`.

## Decisions

### D1. A new anonymous volume gets a unique name

`anonymous_volume` stops hashing the target. A creation draws `<project>_<service>_anon-<10 hex>` from `secrets.token_hex(5)` through a function the tests replace, labels it as before, and retries with a new token if the runtime says the name exists (three attempts, then exit 1 naming the volume). The labels remain the way to find a project's anonymous volumes; the target is stored in the `.anonymous` label.

*Alternatives:* keep the deterministic name and rename the old volume (Apple `container` has no rename); add a generation counter to the name (needs a listing and a race with concurrent `up`; a random token needs neither).

### D2. Reuse comes from the existing container's mounts

For `recreate` without `-V`, the planner takes each anonymous target and looks at the mounts of the service's existing container: a `volume` mount whose destination equals the target decides the volume to mount, provided the volume still exists. If there is none, the target is new and gets a fresh volume, seeded from the image. For `create` and for `-V`, every target is new. The planner receives the container row `cmd_up` already fetched; no label or file is added to the container.

Any `volume` mount at the destination is reused, labelled or not, as Compose does with the previous container's volumes. A volume made by the previous change (deterministic name) is therefore found through the container, and a leftover one with no container is an orphan like any other.

*Alternatives:* store the volume name in a container label (shim-owned state that can drift from the runtime); look up by labels and target (two volumes can carry the same target after `down`, so the choice would be a guess).

### D3. No implicit deletion

`_discard_anonymous_volume`, the "in use" stop and the "unlabelled same-name volume" stop are deleted; `_ensure_anonymous_volume` only creates and seeds. A failed seed removes the volume it just created (it is new and cannot hold user data) and nothing else. `up -V` changes which volume is mounted, never what exists.

*Alternatives:* delete behind a flag such as `--prune-anon` (a second way to do what `down -v` does); delete after the new container starts (still destroys what Docker orphans).

### D4. `down -v` removes by label, not by the file

`cmd_down -v` lists the runtime's volumes with `_list_resources("volume")` and removes those whose `com.docker.compose.project` equals the project and which have the `.anonymous` label, plus the declared named volumes it removed before. A removal the runtime refuses is collected, the rest are still removed, and the command exits 1 naming the refused volumes. The file is still required to be readable (exit 64, nothing removed), as today.

Real Docker's `down -v` removes only the volumes of the containers it stops, so an orphan from an earlier `down` survives there. The shim removes it because `down -v` is the only reclaim path it offers; the scope stays the project label, so a foreign or external volume is never touched.

### D5. Fixture S proves the contract in both engines

The live and conformance sequence becomes: write a marker, `up --force-recreate` keeps it, `down`, `up` shows the marker gone and the image content present, and the volume of the first run still exists (its name is read from the container's mounts before `down`), `up -V --force-recreate` shows a third volume, and `down -v` removes the volume of the current container. Only the live test (shim) asserts that earlier orphans disappear with `down -v`; Docker does not do that, so the conformance test must not.

### D6. Evidence identity gains `pyproject.toml`

`EVIDENCE_TREES` stays and a second tuple `EVIDENCE_FILES = ("pyproject.toml",)` is added in both scripts. The hash text is the lines `src <id>`, `scripts <id>`, `tests <id>`, `pyproject.toml <id>`, with ids from `git rev-parse HEAD:<path>`; the dirty check uses `git diff --quiet HEAD -- <path>` for the file. The schema number becomes 2 and the verifier accepts only 2, naming both numbers on a mismatch. The `trees` object records the four ids (the verifier's shape check accepts the extra key).

`README.md` and `LICENSE` are excluded: they do not change what the code does, and including `README.md` would invalidate the evidence on every documentation commit, which the previous design ruled out.

*Alternatives:* hash the built wheel (its name and metadata depend on the tag, which does not exist when the evidence is produced); hash the whole commit tree (any docs change invalidates it).

### D7. One script installs the wheel

`scripts/verify_wheel.py --dist DIR [--expect-version VERSION]` uses only the standard library. It finds exactly one `*.whl` in `DIR`, then:
1. opens it with `zipfile` and checks that `container_docker_shim/cli.py` and `*.dist-info/entry_points.txt` with `docker = container_docker_shim.cli:main` are present;
2. creates a venv in a temporary directory and installs the wheel with its `pip`;
3. runs from a working directory outside the checkout, with a clean environment, `python -c "import container_docker_shim"` and requires the module path to be under the venv's `site-packages`;
4. runs `docker version` with `CONTAINER_DOCKER_SHIM_CONTAINER` pointing to a stand-in script that prints the runtime line, and requires the `Shim:` line to equal `--expect-version` (or, without it, to differ from `0+source`);
5. runs `docker ps` with no `container` on the path and requires exit 1 and the message `Apple container CLI not found`.

It prints one line per check and exits 3 on the first failure, like the other release scripts.

### D8. Build once, test the same files, publish them

`release.yml` gets a `package` job (read-only, `fetch-depth: 0`, Python 3.x) that builds with `python -m build` and uploads `dist/`, a `smoke` job (read-only, matrix `3.9` and `3.x`) that downloads it and runs `verify_wheel.py --expect-version <tag minus v>`, and the `release` job `needs: [checks, smoke]` downloads the same artifact and no longer builds. The tag reaches the script only through `env`. `ci.yml` gets a `wheel` job with the same matrix that builds and smoke-tests on each pull request, without the version expectation.

The upload and download actions are pinned to full commit SHAs like the others; the task looks the SHAs up and records the release each one is.

*Alternative:* build again in `release` and smoke there. It is simpler but tests a different build than it publishes, and puts the install step in the job that holds `contents: write`.

### D9. The live tier waits for conditions

The cause of the `fixture_b` flake is not known at this point, so the first task reproduces it (the test five times, with the container state and logs captured when it fails) before any change. The fix replaces fixed delays with a shared `wait_until(check, timeout, describe)` helper in `tests/` that returns on the first true check and fails with `describe()` and the last state on timeout. The acceptance is three consecutive complete live runs on `container 1.5.0` with no `shimtest-` object left.

### D10. The first evidence is the last step

The evidence binds to the code, so it is produced after every code and test change has landed on the branch that will be tagged, from a clean working tree, with `python3 scripts/validate-runtime.py --evidence`. The file is committed on its own (a `docs/evidence` change keeps the hash valid). Pushing it needs the user's go-ahead and, over SSH, the VPN off.

### D11. Documentation

The README's anonymous volume text, the **Breaking change** note and the matrix rows are rewritten to the new behaviour (orphans, `-V`, `down -v`); `docs/testing.md` describes `wait_until` and `verify_wheel.py`; `docs/releasing.md` gains the wheel check, schema 2 and the `pyproject.toml` input; `tests/test_docs.py` replaces the assertions that pinned the previous wording.

## Risks / Trade-offs

- [Orphans fill the disk] -> `down -v` removes them and the README says how to list them; the shim never deletes on its own. A future notice or prune command is possible and needs no change to this design.
- [Reuse by mount depends on the inspect shape] -> `_mount_type` already reads `{"volume": {"name": ...}}`; live fixture S exercises it on 1.5.0, and a unit test feeds the real shape.
- [A reused volume that is not anonymous (a named volume at the same destination in the old container)] -> Compose does the same; documented, and `down -v` only removes labelled anonymous volumes.
- [Python 3.9 may fail] -> the task runs the unit suite and the smoke with `/usr/bin/python3` (3.9.6) first; if the code needs newer syntax or the runner has no 3.9, raise `requires-python` to the lowest version that passes and record it in the spec scenario.
- [The upload and download actions are new third-party code] -> pinned to SHAs, read-only permissions, no credentials; fallback is D8's alternative.
- [The fixture B flake may be a runtime bug, not a test bug] -> then the task stops and reports; the evidence cannot be written until the live tier passes completely.
- [A stand-in for `container` hides a real incompatibility] -> it only proves the package starts and reports its version; behaviour is covered by the live and fake tiers.

## Migration Plan

Nothing was released, so no user has a published behaviour to migrate. A machine that ran earlier builds may hold anonymous volumes with the old deterministic names: containers that mount them keep them, and the rest are labelled orphans removed by `down -v`. Rollback is reverting the commits; the shim stores no state.
