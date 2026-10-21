# Proposal

## Why

Scanning the 39 real Compose files under `~/source-code` with the shim's own
gate (read-only, no runtime call) showed that most projects already run, and
that the ones that do not fail for three reasons the shim can reduce: anonymous
volumes (`- /app/node_modules`) are refused with the unhelpful "mount source is
required", the common `local`-driver bind-volume idiom (`driver_opts: {type:
none, o: bind, device: /path}`) is refused although it is just a bind mount, and
several projects could not be judged at all because the only way to run the gate
is a real `up`. Separately, a review found two release-quality defects: the
runtime validator exits 0 when it ran no live test, and the README documents a
`uv tool install` command that uv rejects. The conformance test for
`network_mode: none` also fails on Docker Desktop because it asserts on kernel
placeholder entries. Evidence and reproductions are in `findings.md`.

## What Changes

- Support **anonymous volumes**. A volume entry without a source becomes a
  project-labelled Apple volume with a deterministic name, reused when the
  service is recreated, removed by `down -v`, and populated on first creation
  with the image's content at the target (Docker's copy-up, which Apple does not
  do). If the content cannot be copied, the volume is removed and the command
  fails instead of starting the service with an empty directory.
- Translate **local-driver bind volumes**: a top-level volume with `driver:
  local` (or none) and `driver_opts` `type: none`, `o: bind` (optionally `rbind`,
  `ro`, `rw`) and an absolute `device` is mounted as a bind mount of that path.
  Any other `driver_opts` stays refused, and the message now names the option.
- Treat the network option `com.docker.network.bridge.name` as `NOT APPLIED`
  (it only names a Linux bridge); every other network `driver_opts` stays
  refused.
- Keep `network_mode` refused and **not waivable** (nothing safe exists), but make
  the message value-specific and actionable (`host`, `none`, `service:`,
  `container:`), pointing to published ports and `host.docker.internal`.
- Add `docker compose --dry-run up`: run normalization, the capability gate,
  mount and build-secret pre-flight and the read-only resource checks, print the
  plan, and create nothing. Other commands refuse `--dry-run` with exit 64.
- Make `scripts/validate-runtime.py` exit non-zero (3) when the live tier was
  requested but did not run, or when a matrix entry claiming `live` evidence has
  no executed test; `--skip-live` stays an explicit read-only mode that says so.
- Fix the `uv tool install` command in `README.md` and `docs/releasing.md`
  (`--from <git url> docker-for-apple-container-fork`) and test the documented
  command shape.
- Fix the `n-network-none` conformance assertion to assert on the interfaces that have an address (`ip -o addr`)
  instead of listing `/sys/class/net`.

Out of scope: copy-up for **named** volumes (same gap, existing behaviour, recorded
as F7 in `findings.md`), the `lost+found` directory Apple's ext4 volumes carry,
`network_mode` execution, `ipam`, runtime `secrets`/`configs`, loading `.env`
values the user did not provide.

## Capabilities

### New Capabilities
- `compose-anonymous-volumes`: lifecycle, ownership, removal and first-use
  population of anonymous volumes.
- `compose-resource-compatibility`: how top-level volume and network
  declarations and `network_mode` that the runtime cannot express are translated,
  noted, or refused with an actionable message.
- `compose-dry-run`: side-effect-free evaluation of `up` for compatibility
  checking.

### Modified Capabilities
- `test-tiers`: validation of a candidate runtime cannot succeed without
  executed live evidence; conformance assertions must not depend on the host
  kernel's static entries.
- `fork-distribution`: the documented install commands must be valid as written.

## Impact

- Code: `src/container_docker_shim/compose.py`, `compose_capabilities.py`,
  `compose_resources.py`, `mounts.py`, new `compose_resource_compat.py` (pure),
  `scripts/validate-runtime.py`.
- Tests and fixtures: unit and fake-runtime tests, new live and conformance
  fixtures (`s-anonymous-volume`, `t-local-bind-volume`), the
  `n-network-none` test, a `validate-runtime` test, a docs test.
- Docs and matrix: `README.md`, `docs/testing.md`, `docs/releasing.md`,
  `tests/compat/matrix.json`.
- Behaviour change: Compose files with anonymous volumes or local bind volumes
  that exit 64 today will start. Nothing that works today changes meaning.
- The supported runtime stays `container 1.5.0`.
