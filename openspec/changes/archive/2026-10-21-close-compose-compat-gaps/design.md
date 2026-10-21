# Design

## Context

- The default path is `docker-compose config --format json` -> `convert_model` ->
  `Project` -> `_validate_plan` (gate, mount and build-secret pre-flight) ->
  `_check_resources` (read-only) -> `_ensure_network`, `_ensure_named_volume`,
  image resolution, `container run`. `down -v` removes the volumes returned by
  `_removable_names`, which requires the model **and** the project label.
  See `proposal.md` for motivation and `findings.md` for evidence (F1-F8).
- The previous change made `mounts.py` the one mount policy and split mount
  translation into a pure `_service_mounts` and one effect, `_emit_planned`. This
  change extends that shape; it does not add a second path.
- Apple `container` 1.5.0: `-v /path` makes a runtime-owned `anonymous` volume
  that `rm` leaves behind; new volumes are empty apart from `lost+found`; there
  is no host network. `container run` has `--entrypoint` and `--rm`.
- The official normalizer emits an anonymous volume as `{type: volume, target}`
  without `source`, and keeps a top-level volume's `driver_opts` verbatim
  (`o`, `type`, `device`).

## Goals / Non-Goals

**Goals:**
- Services that use anonymous volumes or the local bind idiom start with the
  data Docker would show them, or fail before the service starts, never
  silently with different content.
- Every decision that needs no runtime stays in a pure module with unit tests.
- A project can be checked for compatibility with no side effects.
- The validator and the install documentation tell the truth.

**Non-Goals:**
- Copy-up for named volumes (F7), removing `lost+found`, `network_mode`
  execution, `ipam`, driver options other than the two listed, anonymous volumes
  for `docker run -v /path` (its behaviour is unchanged), dry run for commands
  other than `up`.

## Decisions

### D1. Pure `compose_resource_compat.py`

One new module with no runtime access: `local_bind(volume_spec) -> LocalBind |
Problem` (accepts `driver` `local` or unset, `type: none`, `o` tokens in
`{bind, rbind, ro, rw}` with at least one of `bind`/`rbind`, absolute `device`;
anything else is a `Problem` naming the option), `classify_network_options(opts)
-> (not_applied, refused)` (only `com.docker.network.bridge.name` is not
applied), and `network_mode_message(value)`. The gate (`compose_capabilities`)
and the translator both call it, so the verdict cannot diverge. This is the same
pattern as `mounts.py`. *Alternative: put the rules in the gate's table.*
Rejected: the translator needs the parsed `device`, and a table of booleans
cannot express "accepted only if".

### D2. Local bind volumes become bind mounts at planning time

`_entry_mount` already replaces a volume key by its runtime name. For a key whose
top-level definition passes `local_bind`, it instead returns a bind `Mount`
(`source=device`, `readonly` from `ro` or from the entry's `read_only`).
`_volume_resource` marks such a volume as not managed by the shim, so
`_ensure_named_volume` never creates it, `down -v` never removes it and
`_check_resources` does not look for it. The existing bind behaviour (a missing
source fails at start with the runtime's error, F5 of the previous change)
applies unchanged.

*Alternative: create an Apple volume and ignore `driver_opts`.* Rejected: the
data would live somewhere else than the user declared.

### D3. Anonymous volume identity

`compose_resources` gains a pure function `anonymous_volume(project, service,
target) -> Resource` with name `<project>_<service>_anon-<first 10 hex of
sha256(target)>` and labels `com.docker.compose.project`, `com.docker.compose.service`
and `com.docker.compose.anonymous=<target>`. `Resources` gains `anonymous: tuple`
computed from the services (pure), so `down -v` can intersect it with the
observed labels exactly like named volumes. A service removed from the file is
not in the model, so its anonymous volumes are left until the file lists the
service again, the same rule named volumes follow.

*Alternative: use Apple's anonymous volumes.* Rejected (F2): not labelled, left
behind by `rm`, no deterministic name to reuse on recreation.
*Alternative: random names like Compose.* Rejected: reuse on recreation would
need state or a label search; a hash of the target is stateless and unique per
service.

### D4. `mount_from_entry` accepts a missing source for volumes

`mounts.mount_from_entry` returns a `Mount(kind="volume", source="")` for a
long-syntax volume entry without a source, after the same attribute validation as
a named volume; the short syntax `/path` is read by the Compose translator
(`_entry_mount`) as "absolute path with no colon". `parse_volume_spec`, used by
`docker run -v`, keeps refusing a source-less volume, so `docker run` is
unchanged. `_entry_mount` then replaces the empty source by the D3 name and tags
the planned mount as anonymous (`service`, `target`).

### D5. First-use population (copy-up)

`_emit_planned` is the only effect, and it now receives the image reference the
container will run. For an anonymous volume that does not exist yet it:
1. creates the volume with the D3 labels;
2. runs a helper: `container run --rm --entrypoint /bin/sh -v <vol>:/shim-dest
   <image> -c 'if [ -d "$1" ]; then cp -a "$1"/. /shim-dest/; fi' sh <target>`
   (the target is a positional parameter, so no quoting problem);
3. on a non-zero exit, `volume rm`s the volume and raises `ShimError(1)` naming
   the service, the image and the target, before the service container is created.

An existing volume is never populated again. The step is skipped when the volume
exists, which is also what makes recreation keep data. `-d "$1"` false means the
path is not in the image, and an empty volume is correct.

*Alternative: `container cp` from a created container.* Rejected: it needs a
created container per volume and the guest agent for directories; the helper is
one command with a defined exit code.
*Alternative: fail every anonymous volume of a shell-less image.* Rejected:
distroless images that never wrote the path would be blocked; the failure is
raised only when the helper fails, and the message says so.

### D6. Network options and `network_mode`

`RESOURCE_ATTRIBUTES["networks"]["driver_opts"]` is replaced by a call to
`classify_network_options`: the bridge name produces a `NOT APPLIED` finding, any
other key a refusal that names it. `network_mode` keeps `waivable=False`; its
message comes from `network_mode_message`, one string per value family
(`host`, `none`, `service:`/`container:`, other).

### D7. `--dry-run` is a global flag, evaluated only for `up`

`main` parses `--dry-run` with the other global options and threads it to
`_dispatch`. For `up` it runs `_load_project`, `_validate_plan`,
`_check_build_secrets` and `_check_resources`, all of which are already free of
side effects, then prints the plan from the same ordered service list that `up`
uses and returns. `_resolve_image` is not called, so no image is pulled or built.
For any other command it exits 64. The test uses the recording fake runtime and
asserts that every recorded call is on an allow-list of read-only verbs, which is
stronger than the existing `assert_nothing_was_created`.

### D8. Validator exit statuses

`validate-runtime.py` ends with: 0 (everything requested ran and held, or
`--skip-live` with no problem), 1 (a check failed: a missing Apple flag, a failed
test, a runner error), 2 (usage or invalid matrix, unchanged), 3 (incomplete: the
live tier was requested but no test ran, or a `live` entry's test did not run).
When both 1 and 3 apply, 1 wins. The summary always ends with a line stating which
case it is. `main` is split so a test can inject `run_live` and the runtime.
`check_entry` splits an `apple` field on `;` and every part must hold (a command
part by its own `--help` exiting 0, a lone global option such as `--version` by
appearing in `container --help`), so that 0 is reachable on the supported
runtime (F9).

### D9. Documentation test for install commands

A unit test reads `README.md` and `docs/releasing.md`, joins backslash-continued
lines, and for each `uv tool install` command asserts one of the two valid forms
(`--from git+<url>@<ref> <package>` or `"<package> @ git+<url>@<ref>"`), rejects
`--from <package>` followed by a URL, and checks that the command in both files is
identical. The command itself was verified against uv 0.9.17 in an isolated tool
directory (F6); an opt-in check in the same test module may repeat that with a
local path when `uv` is on `PATH` and `SHIM_TEST_UV=1`.

### D10. Conformance fix

`n-network-none` runs `ip -o addr` and asserts that the set of interface names
that have an address is `{lo}`. Verified on Docker Desktop 29.9.1: both
`/sys/class/net` and `/proc/net/dev` list down tunnel devices (`tunl0`, `gre0`,
`sit0`, ...) that the kernel creates in every network namespace, so the interface
list is not a usable signal; a default-network container additionally has
`eth0` with an address, so the check discriminates.

## Risks / Trade-offs

- [The helper container needs `/bin/sh` and `cp` in the image] -> the failure is
  loud, removes the new volume and names the image; a distroless image whose
  target is absent from it still works because the helper is only needed when it
  runs, and the message tells the user to use a named volume.
- [A crash between volume creation and population leaves an empty volume that is
  never populated] -> the helper runs immediately after creation and failure
  removes the volume; a killed process is the residual risk, documented with the
  fix (`docker compose down -v`).
- [`lost+found` appears at the root of every Apple volume, including copied
  ones] -> unchanged, existing difference (F7); documented in the README.
- [Local bind volumes expose any host path the file names] -> same exposure as a
  bind mount in the file today; `o` accepts no flag that widens it, and relative
  or missing `device` is refused.
- [`--dry-run` could drift from `up`] -> both call the same pre-flight functions,
  and the test fails if a future pre-flight step performs a non-read-only call.
- [Validator exit 3 will fail CI on a runner without cached images, because a
  live test is skipped] -> that is the point; the runner must have the pinned
  images, as `docs/testing.md` already says.

## Migration Plan

No data migration. Files that were refused now start; anonymous volumes created
by earlier versions do not exist (they were refused). Rollback is a revert; the
labelled volumes remain and can be removed with `docker volume rm` by name.

## Open Questions

- Exact wording of the `--dry-run` plan lines. Deferrable: the specs fix only the
  exit codes, the absence of side effects and the footer.
