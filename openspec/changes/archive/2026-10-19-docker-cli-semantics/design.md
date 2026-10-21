# Design

## Context

`_parse_run_options` and `cmd_build` in `cli.py` are `if/elif` chains over
option names, with option sets lifted to module level (`RUN_*`, `BUILD_*`) and
checked against the matrix. Most Docker options that have no Apple counterpart
are already refused by the final `else: unsupported docker run option`, so the
semantic risk is concentrated in options that share a name or short alias with
Apple's CLI, and in options forwarded verbatim whose value dialect differs.
Apple `container` 1.5.0 facts used here (from `container run --help`, read-only):
`-c, --cpus` (a count), `-m, --memory` (1 MiB granularity, optional suffix),
`--mount type=,source=,target=,readonly`, `-v` (bind), `--network name[,mac=,mtu=]`,
`--runtime` (runtime handler), `--arch`, `--no-dns`, `--read-only-path`,
`--masked-path`, `--virtualization`.

`cmd_ps` in `compose.py` prints a four-column tab separated table and the
dispatcher builds its flag set from `_parse_flags(bools=...)`, which rejects
every unknown option with exit 64. Motivation and findings: `proposal.md`.

## Goals / Non-Goals

**Goals:**
- A short, reviewed audit of the options that can silently diverge, kept as
  data and enforced by a test.
- Fix the confirmed divergences (`-c`, named volumes, `--runtime`, `--network`
  modes, `--mount` dialect, memory units) and the `compose ps` gap.
- Keep the translator a pure function of argv wherever possible, so each fix is
  testable without the runtime.

**Non-Goals:**
- Supporting Docker options that Apple lacks (they stay refused).
- Go-template rendering beyond the existing bounded subset.
- Reproducing Docker's internal CPU scheduling semantics.

## Decisions

### D1. The audit is a table with a test, not prose

`tests/option_semantics.py` holds rows `(command, option, docker_meaning,
apple_meaning, decision, evidence)` for the options below. A test derives the
"risky set" from code (short aliases, options forwarded verbatim whose Apple
flag name equals the Docker name, options with unit or dialect conversion) and
fails for any member without a row. Matrix entries link to the row through their
note. This avoids a full re-audit of ~100 options while making the next
divergence a failing test.

| Option | Docker | Apple 1.5.0 | Decision |
|---|---|---|---|
| `-c` | `--cpu-shares` (weight) | `--cpus` (count) | refuse (`-c`, `--cpu-shares`) |
| `--cpus` | CFS quota, fractional | count; fractional unverified | spike C1; apply or refuse |
| `-m/--memory` | bytes without suffix | MiB granularity | convert bytes, refuse sub-MiB |
| `-u/--user` | uid/gid or names | same | keep (live evidence exists) |
| `--cap-add/-drop` | names | same, normalised | keep |
| `--network` | `none`/`host`/`bridge`/`container:`/name | name only | refuse, map `bridge`, forward |
| `-v name:` | named volume | bind only | translate to volume |
| `--mount` | `src`, `dst`, `ro`, `type=volume` | `source`, `target`, `readonly` | translate aliases, refuse rest |
| `--runtime` | OCI runtime (`runc`) | runtime handler | refuse |
| `--arch`, `--no-dns` | n/a | exists | keep as extension |
| `--env-file` | bare `KEY` inherits host value; `#` comments | `key=value` (unverified) | spike C2 |
| `kill -s`, `stop -s` | name, `SIG`name, number | unverified | normalise (C3) |
| `--pids-limit`, `--security-opt`, `--privileged` | restrictions | none | existing waiver policy |
| `--device`, `--ipc`, `--pid`, `--group-add`, `--cpu-shares`, ... | various | none | already refused by default branch |
| `build --rm/--force-rm` | clean-up of intermediates | none | no-op, justified |
| `build -m/--cpus` | `-m` memory limit; `--cpus` absent | builder resources | keep `-m`, `--cpus` as extension |

### D2. Refuse rather than convert when the unit is ambiguous

For `-m` a bare integer is converted to MiB only when it is an exact multiple
of 1 MiB (`536870912` → `512M`); `512` is refused with the reason, since
passing it through would give 512 MiB where Docker means 512 bytes. Fractional
CPUs follow spike C1: if Apple accepts them, forward unchanged; if not, refuse
instead of rounding.

### D3. Volume source classification reused from Compose

Compose already has `_is_host_path`. The CLI uses the same predicate so
`docker run -v` and Compose agree. Named volumes are created through the same
`volume create` call (label-free; there is no Compose project) and mounted with
the form chosen by spike S1 in `compose-capability-gate` (shared helper in a
small `mounts.py` used by both). Without S1, named-volume `ro` is refused.

### D4. `--mount` as a parsed structure

Parse the comma list into keys, map aliases, validate type and keys, then
re-emit in Apple's syntax. Unknown keys exit 64. This replaces verbatim
forwarding and gives the volume/`readonly` path a single implementation.

### D5. `compose ps` from labels, in two renderers

`_project_containers(project)` already returns rows. Add `render_table(rows)`
and `render_json(rows)` as pure functions; fields come from `container list`
JSON plus labels. Exit code, health and ports use data already available
through `container inspect`; fields that Apple does not expose get Docker's
zero value (empty string, `0`, `[]`), documented in the matrix. The JSON layout
(array versus one object per line) is captured from the real Docker Compose
release on a Docker host in task 1.1 and encoded in a fixture, not guessed.
Hermes and OpenClaw tests are extended to the fields they actually read.

### D6. Documentation claims are tested

`tests/test_docs.py` gains a small check: the README sentences listing output
formats are generated from (or compared with) matrix entries of kind
`format`, one per command/format pair, each with `status` and a `test` id. This
turns "README says it works" into "a test exists that runs it", closing the
gap behind finding 8.1.

## Risks / Trade-offs

- [Scripts that use `-c N` break] → exit 64 with a one-line fix in the message;
  no silent behaviour change either way.
- [Named volume semantics differ in detail from Docker (copy-up from image)]
  → documented in the matrix note for `run -v`; same as Compose today.
- [`compose ps` fields Apple cannot fill] → zero values and a matrix note,
  except `ExitCode`: the runtime exposes no numeric exit code, so `--format
  json` refuses (exit 64) a listing that includes an exited container instead
  of printing 0; the default table omits the code.
- [JSON layout differs between Compose releases] → the conformance fixture
  records the layout of the tested release (5.6.0); the README names it.
- [Network mode refusal breaks a script using `--network host`] → previously a
  runtime error, so no working script exists to break.

## Migration Plan

Land refusals first (`-c`, `--runtime`, network modes) with their tests, then
the volume and `--mount` work (depends on S1 from `compose-capability-gate` for
read-only), then `compose ps`. Each step is revertible on its own.

## Open Questions

- Whether `--cpus` fractions should be rounded up with a notice instead of
  refused if Apple only accepts integers; decide with the C1 evidence, it only
  changes one function.
