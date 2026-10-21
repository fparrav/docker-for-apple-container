# Design

## Context

`compose.py` (3000 lines) translates a normalized service dictionary into
`container run` arguments. Today:

- `UNSUPPORTED_SERVICE_KEYS` (5 keys) only warn, `SECURITY_OPTION_KEYS` (5 keys)
  go through `security.decide()`, and `SUPPORTED_SERVICE_KEYS` is read only by
  tests. Everything else is dropped without output (`findings.md` 2.1, 2.2).
- `cmd_up` enforces security and dependency rules on the selected services,
  then calls `_ensure_network`, `_resolve_image` (builds) and creates containers
  one by one. Volumes are created lazily inside `_named_volume_args`, which is
  why validation cannot be a simple check at the top of `_build_run_args`.
- The official normalizer (`docker-compose config --format json`) hands over a
  fully resolved model, including long-syntax mounts (`:ro` becomes
  `read_only: true`) and resolved resource names. The built-in parser handles a
  documented subset and refuses the rest.
- Apple `container` 1.5.0 offers `--network <name>` (no `none`), `--mount
  type=...,readonly`, `-v`, `--read-only`, `--tmpfs`, `--cpus`, `--memory`,
  `--ulimit`, `--cap-add/-drop`, `--read-only-path`, `--masked-path`; no
  secrets, no restart policy, no pid/ipc/userns/cgroup options.

Motivation and scope: see `proposal.md`. Evidence: `findings.md`.

## Goals / Non-Goals

**Goals:**
- One pure function that takes the normalized project and the services about to
  be created and returns all refusals, partial-application notices and waivers,
  with no I/O and no runtime calls.
- A classification table that is data, versioned with the code, checked against
  the compatibility matrix, and the only place that decides what a key means.
- A reporting format an agent can grep.

**Non-Goals:**
- Translating secrets or configs (only a spike and a recorded decision).
- A general Compose schema validator: the official normalizer already rejects
  invalid files; this layer decides *can we honour it*.
- Changing how `docker run` flags are parsed (`docker-cli-semantics`).

## Decisions

### D1. A new module with a declarative table

`compose_capabilities.py` holds `SERVICE_KEYS`, `BUILD_KEYS`, `MOUNT_OPTIONS`
and `RESOURCE_ATTRIBUTES`: `name -> Rule(status, effect, waivable, reason)`,
where `effect` is `restrict`, `behaviour` or `inform`.

- `restrict` (omission makes the container more permissive or loses data):
  UNSUPPORTED is refused; waivable by key name except isolation.
- `behaviour` (omission changes how the service runs): PARTIAL, applied as far
  as possible, always reported.
- `inform` (no runtime meaning): accepted silently.

`validate(project, services) -> Report(refusals, notices)`; `cmd_up` raises
`ShimError(…, 64)` from the report. Values matter, not only keys:
`restart: "no"`, `network_mode` absent, `deploy.replicas: 1` produce nothing.

*Alternative: extend `UNSUPPORTED_SERVICE_KEYS` and `_warn_unsupported`.*
Rejected: it keeps three overlapping tables, and the failure we found (silent
unknown keys) is a property of having no complete vocabulary.

*Alternative: fail only on a deny-list.* Rejected: Compose gains keys over time
and a deny-list is open by construction; the allow-list plus waiver is the
fail-closed form. The official normalizer limits the noise because it rejects
typos before the shim sees them.

### D2. Validate before any provisioning, on the plan

`cmd_up` today creates networks before anything else. Order becomes: load and
normalize, compute the service plan (selected services plus dependencies, see
`compose-resource-identity`), validate, then provision. `_enforce_run_security`
and `_validate_dependencies` move inside the same step so one run reports all
problems. Build steps (`_resolve_image`) also run after validation, so a refused
project never builds an image.

### D3. Network isolation is not waivable; other restrictions are

A waiver of a *restriction* means "run it weaker", which for `network_mode:
none` means "give it a network", a silent grant of connectivity. The existing
precedent (`--network=none` is refused for `docker run` with no env escape)
confirms the intent; Compose and `security.RULES` (`network-none`) follow it.
`security.Rule` gains `waivable: bool`. Resource limits and capability
restrictions stay waivable by explicit name because running without a pids
limit is a decision the user can take; the README already documents this.
`hostname`, `add-host`, `network-none` leave the waivable list (finding 9.3).

### D4. A stable report, not a new exit code or channel

`REFUSED:` and `NOT APPLIED:` prefixes on standard error. Exit status keeps
Docker conventions (0 when the command ran, 64 when refused). A new exit code
for "ran with reductions" would break scripts that treat non-zero as failure,
and a strict mode variable is not needed because the grep-able prefix lets an
agent decide. Standard output stays clean so container ids and JSON parse.

### D5. Read-only and mount options: translate through `--mount`

Long-syntax mounts become `--mount type=<bind|volume|tmpfs>,source=…,target=…`
plus `readonly`, replacing the mix of `--mount` for binds and `-v` for volumes,
so one code path carries the mode. Which of `type=volume` and `-v name:t:ro`
Apple actually honours is verified by spike S1 (a write must fail) before the
mapping is chosen; if neither works, named-volume `read_only` is refused
(spec: "Runtime cannot enforce it"). Short-syntax parsing keeps `ro`, `rw`,
`z`, `Z`, `cached`, `delegated` and refuses the rest.

### D6. Secrets and configs: alternative B now, A only after evidence

| | A. Read-only file mount | B. Refuse |
|---|---|---|
| Confidentiality | file stays on host; container sees host mode and owner only; `uid/gid/mode` cannot be applied | nothing exposed |
| Needs | Apple single-file mount (unverified), no parent-directory mount (would expose siblings) | nothing |
| `environment:`/`content:` sources | need a temp file with cleanup, which conflicts with "no shim-owned state" | refused |
| External secrets | no Engine to resolve them | refused |
| Risk | exposing more than requested through a coarse mount | services that used `secrets:` must change the file |

B ships in this change. Spike S2 tests a single-file read-only bind with a
non-root process; if it works with host permissions only, a follow-up change
adds alternative A for `file:` secrets and configs in short syntax and long
syntax without `uid/gid/mode`, refusing the rest. Decision: no emulation that
puts a secret in an environment variable, a label or the process list.

### D7. Build keys reuse the CLI

`_run_build` builds an argv for the same translator used by `docker build`
instead of its own partial list, so `target`, `labels`, `no_cache`, `pull`,
`secrets` and `ssh: default` behave as the CLI does and unsupported keys are
refused by one table.

## Risks / Trade-offs

- [Stricter `up` breaks files that worked by accident] → refusal names the key
  and the waiver; BREAKING is listed in the proposal; README documents the
  migration; the `NOT APPLIED` token makes partial runs visible.
- [A Compose release adds a harmless key] → refused until classified; waiver by
  key name unblocks the user, and the matrix test makes the fix a one-line
  table entry.
- [Table drift from Compose] → `tests/test_compose_capabilities.py` loads the
  official key list from `docker-compose config` fixtures where available and
  fails on keys the table does not know.
- [Spike S1 fails: no read-only named volumes] → documented limit, refusal
  instead of enforcement; fixtures using `:ro` on named volumes change to bind
  mounts.
- [Plan computed twice (gate and RES)] → one `plan_services()` function owned
  by `compose-resource-identity`; until it lands the gate validates the
  existing selection.

## Migration Plan

Land the table and the validation phase first (refusals for unknown, network,
secrets), then mounts after spike S1, then build keys. Each step keeps the
suite green. Rollback is reverting the commit; no persisted state exists.

## Open Questions

- Whether `restart: on-failure` deserves a refusal instead of a notice for
  workloads that depend on it; deferrable, the notice already makes it visible.
