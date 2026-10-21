# Tasks

Conventions for every task:

- Python 3.9 standard library only, `from __future__ import annotations`, unittest only. Tests are top-level modules in `tests/` with a module-level `TIER`; live tests are opt-in (`SHIM_TIERS=live`), name objects `shimtest-<topic>-<short uuid>`, clean up by exact name, never prune. Run `python3 -m unittest discover -s tests -v` before closing a group.
- Each task lists `Priority` (P0 must ship, P1 should ship), `Files`, `Depends` and `Done`. Tasks in the same group touch different files unless stated. `compose.py` is a hotspot: every task that edits it is chained through `Depends`, so no two run at once.
- Matrix entries (`tests/compat/matrix.json`) land in group 6; regenerate the README block with `python3 scripts/render_compat_table.py --write README.md`. A matrix row may claim `live` or `conformance` only when a test in this change proves it.
- The supported runtime stays `container 1.5.0`; do not touch `SUPPORTED_CONTAINER_VERSION`. Do not stage `openspec/` or `AGENTS.md`.
- Reproductions are in `findings.md`; recreate them as fixtures, do not point tests at `~/source-code`.
- Live tests that create anonymous or helper objects must remove them by exact name, and a task that runs the live tier checks `container volume list`, `container list --all` and `container network list` for leftovers afterwards.

## 1. Independent fixes (no dependency on the rest)

- [x] 1.1 Fix the `n-network-none` conformance assertion to assert that only `lo` has an address (`ip -o addr`, design D10) @worker:local
  - Priority: P1. Files: `tests/test_conformance_fixtures.py`. Depends: none.
  - Done: the default tier still skips it with a reason; with `SHIM_TIERS=conformance SHIM_CONFORMANCE_DOCKER=/usr/local/bin/docker` the test passes on Docker Desktop (the coordinator runs it).
- [x] 1.2 Make `scripts/validate-runtime.py` exit 0/1/2/3 as in design D8 and always print which case applies @worker:local
  - Priority: P0. Files: `scripts/validate-runtime.py`, `tests/test_validate_runtime.py` (new, `TIER = "unit"`, imports the script with `importlib`), `docs/testing.md` (exit status table). Depends: none.
  - Tests: injected runtime and injected `run_live`: every live test skipped -> 3 and "live tier: not run"; live ran and a `live` entry's test missing -> 3; a failed test -> 1; a missing Apple flag -> 1; both 1 and 3 -> 1; `--skip-live` without problems -> 0 and the summary says so; all held -> 0; invalid matrix -> 2.
  - Done: `python3 -m unittest tests.test_validate_runtime -v` passes with no runtime; `.github/workflows/runtime.yml` needs no change (the job already fails on a non-zero exit) and a one-line comment there states the exit meaning.
- [x] 1.3 Fix the `uv tool install` command in `README.md` and `docs/releasing.md` and add the documentation test (design D9) @worker:local
  - Priority: P0. Files: `README.md`, `docs/releasing.md`, `tests/test_docs.py`. Depends: none.
  - Tests: both files contain the same valid `uv` release command; the old inverted form is rejected by the test (assert it on an inline sample); `pipx` command unchanged.
  - Done: `python3 -m unittest tests.test_docs -v` passes; the coordinator runs the documented command with a local path in an isolated `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` and sees the `docker` executable.
- [x] 1.4 Make the validator check compound `apple` fields part by part (finding F9, design D8) @worker:orchestrator
  - Priority: P0. Files: `scripts/validate-runtime.py`, `tests/test_validate_runtime.py`. Depends: 1.2.
  - Tests: both parts hold -> held; a missing command part or a missing global option part -> missing; a single command is unchanged.
  - Done: `scripts/validate-runtime.py --skip-live --container /opt/homebrew/bin/container` reports 0 problems and exits 0 on container 1.5.0.

## 2. Pure foundations (no behaviour change yet)

- [x] 2.1 Add `src/container_docker_shim/compose_resource_compat.py`: `local_bind`, `classify_network_options`, `network_mode_message` (design D1) @worker:local
  - Priority: P0. Files: `compose_resource_compat.py` (new), `tests/test_compose_resource_compat.py` (new, `TIER = "unit"`). Depends: none.
  - Tests: `local_bind` accepts `{type: none, o: bind, device: /a}`, `o: bind,ro`, `rbind`, `driver: local` and no driver; refuses `type: nfs`, `type: tmpfs`, relative `device`, missing `device`, `o: bind,uid=1`, `o: ro` alone, `driver: other`; every refusal names the volume option; network options: bridge name alone -> not applied, bridge name plus `com.docker.network.driver.mtu` -> the second is refused and named; `network_mode_message` for `host`, `none`, `service:db`, `container:x`, `bridge` and an unknown value each contains the value and a different hint (host mentions `host.docker.internal`).
  - Done: `python3 -m unittest tests.test_compose_resource_compat -v` passes with no filesystem or runtime access.
- [x] 2.2 Add anonymous volume identity to `compose_resources.py`: `anonymous_volume(project, service, target)` and `Resources.anonymous` (design D3) @worker:local
  - Priority: P0. Files: `src/container_docker_shim/compose_resources.py`, `tests/test_compose_resources.py`. Depends: none.
  - Tests: same inputs give the same name; different target, service or project give different names; the name contains only characters Apple accepts for volume names; labels carry project, service and target; `resolve` lists the anonymous volumes of a model with short and long entries and ignores bind, tmpfs and named entries; `removable` treats them like named volumes (label must match the project).
  - Done: `python3 -m unittest tests.test_compose_resources -v` passes and the existing resource tests are unchanged.
- [x] 2.3 Let `mounts.mount_from_entry` return a source-less volume mount for compose (design D4) @worker:local
  - Priority: P0. Files: `src/container_docker_shim/mounts.py`, `tests/test_mounts.py`. Depends: none.
  - Tests: long entry `{type: volume, target: /x}` and `{type: volume, target: /x, volume: {}}` give `kind="volume"` with an empty source; `read_only: true` is kept; `volume.subpath` and `volume.nocopy` are still refused; `parse_volume_spec("/x")` and `docker run -v /x` keep their current verdict (assert the existing message).
  - Done: `python3 -m unittest tests.test_mounts tests.test_cli -v` passes.
- [x] 2.4 Contract test CT4: the gate and the translator agree on every volume and network option, with a table-driven set of resource declarations @worker:local
  - Priority: P0. Files: `tests/test_contract_resource_options.py` (new, `TIER = "unit"`). Depends: 2.1.
  - Done: the test passes against `compose_resource_compat` alone now and is extended in 3.2 to the translator; it fails when a case is removed from one side (assert this with a patched verdict).

## 3. Gate and translator

- [x] 3.1 Gate: value-sensitive `driver_opts` for volumes and networks, bridge name as `NOT APPLIED`, specific `network_mode` messages (design D6) @worker:local
  - Priority: P0. Files: `src/container_docker_shim/compose_capabilities.py`, `tests/test_compose_capabilities.py`. Depends: 2.1.
  - Tests: the local bind idiom produces no finding; `type: nfs` is refused with the volume and option named; the bridge-name-only network produces a `NOT APPLIED` finding; a second option is refused; `network_mode` for each value family returns the specific message and is still not waivable even when named in the waiver variable.
  - Done: `python3 -m unittest tests.test_compose_capabilities tests.test_compat_matrix -v` passes.
- [x] 3.2 Translate local bind volumes in `compose.py`: `_entry_mount`, `_volume_resource`/`_check_resources` treat them as unmanaged, `down -v` ignores them (design D2) @worker:orchestrator
  - Priority: P0. Files: `src/container_docker_shim/compose.py`, `tests/test_compose.py`, `tests/test_contract_resource_options.py`. Depends: 2.1, 2.4, 3.1.
  - Tests (recording fake runtime): the Thingboard-style volume yields `--mount type=bind,source=<device>,target=...`; `o: bind,ro` adds `readonly`; no `volume create` and no `volume inspect` for that key; `down -v` makes no `volume rm` for it; two services mounting the same key both get the bind; the refused variants exit 64 with nothing created.
  - Done: `python3 -m unittest tests.test_compose tests.test_contract_resource_options -v` passes.
- [x] 3.3 Anonymous volumes in `compose.py`: planning, creation with labels, first-use population, `down -v` (design D3-D5) @worker:orchestrator
  - Priority: P0. Files: `src/container_docker_shim/compose.py`, `tests/test_compose.py`. Depends: 2.2, 2.3, 3.2.
  - Tests (recording fake runtime, extended with `volume create/inspect/rm` and the helper `run`): a source-less short and long entry mounts the D3 volume; a second `up` makes no `volume create` and no helper run; a failing helper removes the volume, exits non-zero naming service, image and target, and creates no service container; a missing target in the image (helper succeeds) leaves the volume empty; `down` removes none, `down -v` removes exactly the labelled ones and not an unlabelled or foreign volume; a service whose anonymous entry sets `volume.subpath` exits 64 before any call; two targets give two volumes.
  - Done: `python3 -m unittest tests.test_compose -v` passes and the F2 reproduction (TechSuite-style `- /app/node_modules`) reaches `container run` with a labelled named volume, never with `-v /path`.
- [x] 3.4 Document the behaviour in the code-adjacent docs: `docs/testing.md` fixture list and the README Compose section (anonymous volumes and copy-up, `lost+found`, local bind volumes, bridge name notice, `network_mode` alternatives) @worker:orchestrator
  - Priority: P1. Files: `README.md`, `docs/testing.md`. Depends: 3.3.
  - Done: `python3 -m unittest tests.test_docs -v` passes and every key named in the README exists in the specs.

## 4. Dry run

- [x] 4.1 Add the global `--dry-run` flag and the `up` dry run (design D7) @worker:orchestrator
  - Priority: P1. Files: `src/container_docker_shim/compose.py`, `tests/test_compose.py`. Depends: 3.3.
  - Tests: accepted project prints networks, volumes and services in dependency order and the footer, exit 0; the recording fake shows only read-only verbs (allow-list: `--version`, `inspect`, `list`, `image inspect`, `volume inspect`, `network inspect`); a refused key exits 64 with the real `REFUSED:` lines; a managed-name conflict exits 1 like `up`; a missing build secret file for a service that would build exits 64; `--dry-run down|stop|rm|build|pull` exits 64 and calls nothing; `up SERVICE --dry-run` honours the selection.
  - Done: `python3 -m unittest tests.test_compose -v` passes and the recording test fails if a future pre-flight step makes a mutating call (assert with a deliberately mutating stub).

## 5. Live and conformance fixtures

- [x] 5.1 Fixture `s-anonymous-volume` with live and conformance tests @worker:local
  - Priority: P0. Files: `tests/fixtures/compat/s-anonymous-volume/` (new), `tests/test_live_fixtures.py`, `tests/test_conformance_fixtures.py`. Depends: 3.3.
  - Fixture (as built): `alpine:3.20` with the short-form `- /etc/apk` (a path the image has, copy-up) and the long form `{type: volume, target: /var/cache/shim-anon}` (a path it lacks, empty); `REV` env var forces a recreation. Tests: image content visible; a file written to the volume survives a recreate; `down -v` removes the volumes (live: `container volume list` shows none of the project's). Conformance asserts survival across recreate only, because Docker drops anonymous volumes across a full `down`; the same unmodified file runs on Docker.
  - Done: with `SHIM_TIERS=live` then `conformance` both pass using the identical fixture, no leftovers remain, the image is removed by exact name, and the default run reports them skipped with a reason.
- [x] 5.2 Fixture `t-local-bind-volume` with live and conformance tests @worker:local
  - Priority: P0. Files: `tests/fixtures/compat/t-local-bind-volume/` (new), `tests/test_live_fixtures.py`, `tests/test_conformance_fixtures.py`. Depends: 3.2.
  - Fixture: a volume with `driver_opts: {type: none, o: bind, device: ${DATA_DIR}}`, a `.env` override with an absolute temp directory, one read-write service writing a file and one `o: bind,ro` service failing to write. Tests: the file appears in the host directory; the read-only write fails; no volume named after the key exists (live) and the host directory survives `down -v`.
  - Done: live and conformance pass with the identical fixture, no leftovers, default run skips with a reason.
- [x] 5.3 Live test that `network_mode: host` is refused with the host-specific message and creates nothing @worker:local
  - Priority: P1. Files: `tests/test_live_fixtures.py` (reuses `n-network-none`'s listing pattern with an inline compose file). Depends: 3.1.
  - Done: the live test passes and the message contains `host.docker.internal`.

## 6. Matrix and integration

- [x] 6.1 Matrix rows and evidence: `compose volumes.anonymous` (SUPPORTED, live), `compose volumes.driver_opts local bind` (PARTIAL: only the bind idiom), `compose networks.driver_opts bridge.name` (PARTIAL, unit), keep `network_mode` UNSUPPORTED with the new note; regenerate the README table @worker:orchestrator
  - Priority: P0. Files: `tests/compat/matrix.json`, `README.md`. Depends: 3.3, 5.1, 5.2.
  - Done: `python3 scripts/render_compat_table.py --check README.md` exits 0 and `python3 -m unittest tests.test_compat_matrix -v` passes.
- [x] 6.2 Integration checks @worker:orchestrator
  - Priority: P0. Files: none (evidence in the final report). Depends: all.
  - Done: the whole suite passes; `openspec validate close-compose-compat-gaps --strict` is valid; the live tier is run for the new and the touched tests and the conformance tier for fixtures `n`, `s`, `t` (each reported "ran" or "not run" with the reason); no `shimtest` object remains; the F1 scan is repeated and TechSuite (dev) and Thingboard now pass the gate while `orquestador-demo` is still refused with the host-specific message; `scripts/validate-runtime.py --skip-live` exits 0 and the same script without a runtime exits non-zero; the attribution check `git log --format=%B @{u}..HEAD | grep -iE "claude|anthropic|co-authored"` returns nothing for the author's commits.
