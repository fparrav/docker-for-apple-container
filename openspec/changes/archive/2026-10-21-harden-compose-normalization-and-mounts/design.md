# Design

## Context

- The default path is `docker-compose config --format json` -> `convert_model`
  -> `Project(name, model, dir)` -> `compose_capabilities.validate` (inside
  `_validate_plan`) -> `_build_run_args` / `_run_build` -> `container`. The
  built-in parser feeds the same `Project` when `docker-compose` is absent, so
  every fix has to hold for both model sources (the "Stable parser-runtime
  interface" requirement).
- `convert_model` copies `name`, `services`, `networks`, `volumes`. The official
  output also carries top-level `secrets` (already pruned to the ones in use,
  paths absolute) and expands every volume to long syntax; a `:z`/`:Z` short mode
  arrives as `bind.selinux` even on a named volume.
- `cmd_up` runs `_validate_plan` (pure) and then `_check_resources` (read-only)
  before `_ensure_network`, but `_run_build` and the mount translation run later:
  the former inside `_resolve_image`, the latter inside `_create_service`.
  `cmd_build` shares `_validate_plan` and `_resolve_image`.
- `mounts.py` is already the pure mount parser for `docker run`; `cli.py` catches
  its `MountSpecError`, `compose.py` does not.
- Apple `container` 1.5.0: `build --secret id=<k>,src=<path>|env=<VAR>` (the
  runtime reads the variable from its own process environment), `--mount`
  with `type/source/target/readonly` only, no SELinux directive.
- Evidence and reproduction commands: `findings.md`. Motivation: `proposal.md`.

## Goals / Non-Goals

**Goals:**
- Each Compose key the orchestrator or gate reads is either preserved by the
  model or on a documented, tested drop list.
- One policy table for mount options, read by `docker run`, the translator and
  the gate.
- Everything that can be decided without the runtime is decided before the first
  side effect, in the `REFUSED:` report the gate already prints.
- No new process, dependency or runtime state; small pure modules like
  `compose_resources.py` and `compose_plan.py`.

**Non-Goals:**
- Reading or mounting secrets at runtime; SELinux emulation; waivers for
  refused mount options; changing `docker build --secret`; rewriting
  `compose.py`; reading `.env` files in the shim for secret values.

## Decisions

### D1. `convert_model` keeps the build-referenced secrets

`convert_model` adds `secrets`: for every service `build.secrets` entry (string
or `{source,...}`), the definition of `source` from `raw["secrets"]`, copied
whole and un-escaped with `_unescape_dollars`. A non-mapping `secrets` raises
`NormalizerError` like the other sections. The same un-escape is applied to the
`networks` and `volumes` the model already keeps (F4). A module constant
`DROPPED_TOP_LEVEL` lists what is dropped on purpose (`configs`, `x-*`, secrets
no build references), and a test fails when a normalized fixture has an
unlisted top-level key. `Project.secrets = doc.get("secrets") or {}` mirrors
`networks`/`volumes`, so the built-in parser's `doc["secrets"]` works unchanged.

*Alternative: read `project.normalized["secrets"]` in `_build_secret_args`.*
Rejected: the built-in parser has no `normalized`, it leaves two sources of
truth, and the next consumer would repeat the bug. *Alternative: copy all
top-level keys.* Rejected: it would hand unsupported configuration (`configs`,
external secrets) to code that does not check it.

### D2. `compose_build_secrets.py`: pure resolution

`resolve(service, entries, definitions, project_dir)` returns `(secrets,
problems, notices)` where each `BuildSecret` has `id` (`target` or `source`),
`kind` (`file` or `env`) and `value` (absolute path or variable name). Problems:
undefined name, `external: true`, both or neither of `file`/`environment`,
unknown entry or definition attribute. `uid`, `gid`, `mode` give a `NOT
APPLIED` notice (Compose does not pass them; checked in `docker/compose`
`toBakeSecrets`). `check(secrets, isfile, environ)` returns the existence
problems (file missing; variable unset). `to_args(secrets)` returns the Apple
`--secret` list. Nothing reads file contents and no message contains a value
(the variable *name* and path are the only data used). `_build_secret_args`
becomes a three-line call into this module so build and preflight share one
code path.

### D3. Environment secrets: process environment only

Apple reads the variable from the `container build` environment, which is the
shim's own. Compose also reads `.env` and `--env-file`. The shim therefore
requires the variable to be exported and refuses (exit 64, naming the variable)
when it is not, instead of passing an unset variable to a runtime whose
behaviour for that case was not observed.

*Alternative: resolve the value through `docker-compose config --environment`
and inject it into the build child.* Rejected for now: the output is a raw
`KEY=VALUE` dump that cannot safely carry multi-line values (PEM keys are the
likely secret), it moves every variable through one more pipe, and a wrong parse
would produce a silently wrong secret. It can be added later without changing the
refusal contract (the `.env`-only case would start succeeding).

### D4. `mounts.py` owns the mount policy

`mounts.py` gains the table and four pure functions, and nothing else owns a
copy:

- `MODE_RULES`: `ro` -> read-only; `rw`, `cached`, `delegated` -> accepted
  without effect; `z`, `Z` -> refused (SELinux reason, today's text).
- `ATTRIBUTE_RULES` keyed by section and name: `read_only` -> read-only;
  `consistency` -> no effect; `bind.selinux` (non-empty) -> refused;
  `bind.propagation`, `volume.nocopy`, `volume.subpath`, `image`, `tmpfs` ->
  refused; `bind.create_host_path` -> classified by task 3.5 (until then it is
  accepted exactly as today and the matrix says `fake`, not `live`).
- `check_short_modes(mode, target) -> readonly`, raising `MountSpecError`.
- `mount_from_entry(entry, project_dir, volume_name) -> Mount` for the
  long-syntax mapping the normalizer emits (it validates every key against
  `ATTRIBUTE_RULES`, so an unknown nested key is a refusal, not a drop).

`compose_capabilities` deletes `MOUNT_OPTIONS`/`MOUNT_SUBKEYS` and asks these
functions, keeping its `Finding` format. `compose.py` deletes
`_NOOP_MOUNT_MODES`, `_mount_readonly`, `_is_host_path`, `_resolve_host_path`
and `_join_volume`; `docker run` parsing is unchanged apart from reading the
table. Compose-only work stays in `compose.py`: mapping a volume key to the
resolved runtime name (`project.resources`) and creating it.

*Alternative: make the gate the owner.* Rejected: `docker run` has no gate, and
`mounts.py` already holds the `docker run` verdict that must not change.
*Alternative: only delete `z`/`Z` from `_NOOP_MOUNT_MODES` and the gate list.*
Rejected: that fixes the short syntax in the fallback path but not
`bind.selinux`, which is the form the default path produces.

### D5. Pure mount planning in the pre-flight

`_classify_and_translate_volume` splits into a pure `_service_mounts(service,
project) -> list[Mount]` and the existing effect (`_ensure_named_volume`).
`_validate_plan` calls the pure part for every service in the plan and converts
`MountSpecError` into `REFUSED:` lines next to the gate's; `_build_run_args`
reuses the same function. A `MountSpecError` that still escapes is converted to
`ShimError(64)` at the `compose` boundary so no traceback reaches the user.

### D6. Pre-flight ordering

`cmd_up` and `cmd_build` call, before `_check_resources` / `_ensure_network`:
(1) `_validate_plan` (gate, mounts, secret definitions: purely structural);
(2) for the services that will build, `compose_build_secrets.check` with the
real `os.path.isfile` and `os.environ`. A service "will build" when it has
`build`, `--no-build` is absent, and either `--build` was given or its image is
missing (`_image_exists`, a read-only `image inspect`). This keeps Docker's
behaviour that an existing image does not need its secret file, while a
structurally invalid definition fails like `docker compose config` would.

### D7. Repeated `--env-file` and `=` spellings

`main()` collects `env_files: list[str]` (and accepts `--env-file=`,
`--project-directory=`, `--file=`, `--profile=`, `--project-name=`); an empty or
missing value is a 64. The list is threaded as a tuple through `_dispatch`,
`_load_project` and `_normalizer_inputs` into `Inputs.env_files`, which
`build_argv` already emits in order. Paths are passed unchanged and the child
inherits the working directory, which is how the official binary resolves them.
`COMPOSE_ENV_FILES` needs no code with the official binary (the child inherits
it, and the command line wins, verified).
`_require_builtin_subset_inputs` gains: more than one env file -> 64, and
`COMPOSE_ENV_FILES` set -> 64, both with the existing install hint;
`_load_env_file(path, required)` raises for an explicit missing file (exit 1,
`couldn't find env file: <abs>`, the official wording) and stays silent for the
default `.env`. `exec --env-file` is unrelated and untouched.

### D8. Three shared contract tests

All `unit` tier, no runtime: (CT1) every top-level key of every normalized
fixture is in the model or in `DROPPED_TOP_LEVEL`; (CT2) for each option in
the mount table, the `docker run -v`, Compose short (fallback parser), Compose
long (normalizer-shaped model) and gate verdicts agree and match the table;
(CT3) for generated secret definitions the resolver yields arguments or a
problem, never neither, and the official-shaped model and the built-in
parser's model give identical results. They are the safety net for the next key
someone adds to a table.

### D9. Evidence

Fake: argument shape and zero side effects (the fake runtime records every call;
tests assert the absence of `network create`, `volume create`, `build`, `run`).
Official: the gated 5.6.0 tests run the real normalizer on the reproduction
projects. Live: a build-secrets fixture (file and env, with `target`), the
read-only fixture extended to long syntax with a read-write control, and an
SELinux fixture that must exit 64 and create nothing. Conformance: the same
fixtures on Docker Engine (build secrets and repeated env files must behave
the same; `:z` is accepted there, which the test records as a documented
difference rather than a failure).

## Risks / Trade-offs

- [Compose files with `:z`/`:Z` that used to start now exit 64] -> BREAKING
  note in the proposal, README migration text, message names the option and says
  it is safe to remove on macOS; no waiver (see Open Questions).
- [`consistency` and `cached` accepted without effect] -> matches Docker outside
  Desktop; recorded in the matrix as accepted-without-effect, not as supported.
- [Env secrets fail when the variable is only in `.env`] -> explicit refusal with
  the variable name and the fix; never a silent empty secret.
- [Build preflight needs `image inspect` before any other call] -> read-only,
  same kind as `_check_resources`; the duplicate call in `_resolve_image` is a
  cheap inspect.
- [`compose.py` is a merge hotspot for six tasks] -> tasks touching it are
  chained (`Depends`), pure modules and tests land first.
- [Upstream behaviour changes (`toBakeSecrets`, `config` output)] -> pinned to
  Compose 5.6.0 by the gated official tests; the version constant is advanced
  together with fixtures as `AGENTS.md` requires.
- [Apple build with an unset env secret was not observed] -> preflight never
  relies on it; task 4.5 records the live outcome in `findings.md`.

## Migration Plan

No data or state migration. Release notes and README state the SELinux change.
Rollback is a revert of the change; no resource created by earlier versions is
touched. The `Out of scope` items stay refused or unchanged.

## Open Questions

- Should a named waiver (for example `volumes.z` in
  `CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED`) turn the SELinux refusal into a
  `NOT APPLIED` notice for users who share Compose files with SELinux hosts?
  Not decided here: `docker run` has no waiver, and adding one later does not
  change the default refusal, the specs or these tasks.
- Whether Apple builds fail or continue when an environment secret's variable
  is unset (task 4.5 records it; the refusal exists either way).
