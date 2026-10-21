# Findings

Audit of the current tree (HEAD `15ad5f2`, working tree clean apart from
`openspec/`) against `docker-compose` 5.6.0 and `container` 1.5.0. Every row was
reproduced; none is inferred from the earlier review alone. Reproductions used
a scratch directory and read-only calls: the model was loaded with the real
normalizer through `compose._load_project` and the pure translators were called
directly; no container, network, volume or image was created.

## Existing OpenSpec coverage

`compose-capability-gate` and `compose-resource-identity` (both complete)
cover the gate, resource identity and selection. None of the 13 main specs
mentions build secrets, SELinux modes or repeated env files, so no gap was
already covered. `compose-normalization` lists `--env-file` as supported but
has no scenario for more than one file.

## F1. Build secrets are lost after normalization (P1) - CONFIRMED

| Check | Result |
| --- | --- |
| `docker-compose config --format json` for `build.secrets: [npm_token]` | emits top-level `secrets.npm_token = {name, file: <absolute path>}` and `build.secrets = [{source: npm_token}]`; unused top-level secrets are already pruned; `environment:` secrets are emitted as `{name, environment}` |
| `convert_model` output keys | `name`, `services`, `networks`, `volumes` only; `Project.doc` has no `secrets` (`project.normalized` still has it) |
| `_build_secret_args` on the loaded project | `compose: service 'api' build.secrets names 'npm_token', which is not defined in the top-level 'secrets'` (exit 64) |
| Capability gate | reports no refusal for the project: `BUILD_KEYS["secrets"]` is `SUPPORTED`, the gate receives only `networks` and `volumes`, and nothing validates build secrets before the build |
| Existing unit test | `build_args` tests use the built-in parser (`parse_yaml`), so they pass while the default path fails |

Related defects found while verifying, all in scope because the same function
has to change:

- **F1a. Wrong BuildKit id.** Compose derives the id from `target`, falling
  back to `source` (`toBakeSecrets` in `docker/compose` `pkg/compose/build_bake.go`).
  `_build_secret_args` always uses `source`, so a Dockerfile with
  `RUN --mount=type=secret,id=foo` and `target: foo` cannot find its secret.
- **F1b. Environment secrets ignore the Compose environment.** Compose reads
  the variable from the project environment (process, `--env-file`, `.env`).
  Apple's `--secret id=...,env=VAR` reads the `container build` process
  environment only. A variable defined only in `.env` reaches Docker's build
  but not the shim's; whether Apple fails or builds with an empty secret was
  not observed, so preflight must not depend on it.
- **F1c. Late failure.** A missing secret file is not detected by
  `docker-compose config` (it exited 0 with a non-existent `file`). The shim
  discovers it inside `_run_build`, after `_ensure_network` has run in `cmd_up`
  and after earlier services have been built.
- **F1d. External secrets.** `external: true` normalizes to
  `{name, external: true}` with no `file` or `environment`; it must be refused
  for builds with a clear message.
- **F1e. Un-escaped values.** `config` renders every literal `$` as `$$`; the
  model un-escapes services only. Preserved secret definitions need the same
  treatment or a path containing `$` would be wrong.
- Runtime secrets stay separate: with the same secret used by `build` and by
  `services.<name>.secrets`, the service keeps
  `secrets: [{source, target: /run/secrets/<name>}]` and the gate keeps refusing
  it (not waivable).

## F2. SELinux modes are classified three ways (P1) - CONFIRMED

| Stage | `./data:/data:Z` today |
| --- | --- |
| `docker run -v` (`mounts.parse_volume_spec`) | refused: `volume mode 'Z' requests SELinux relabeling, which Apple container cannot represent` |
| Official normalizer, Compose short or long syntax | emits `{type: bind, source: <abs>, target, bind: {selinux: "Z"}}`; for a **named volume** `vol:/v:Z` it emits `type: volume` plus `bind: {selinux: "Z"}` and `volume: {}` |
| Capability gate | `MOUNT_OPTIONS` marks `z`/`Z` `SUPPORTED`; `MOUNT_SUBKEYS["bind"]["selinux"]` is `SUPPORTED`; no refusal |
| `_classify_and_translate_volume`, long-syntax branch (the normalizer path) | reads `source`, `target`, `type`, `read_only` only; `bind.selinux` is never read and the option is dropped |
| Built-in parser, short syntax | `_NOOP_MOUNT_MODES` contains `z`/`Z`, so `_mount_readonly` accepts them and the mode never reaches `mounts.py` |
| Matrix / README | `compose volumes.z`, `volumes.Z` are `SUPPORTED`; `tests/test_compose_capabilities.py::test_modes_that_are_accepted` and `tests/test_compose.py` (`data:/d:z`) assert the accepting behaviour |

Duplicated mount knowledge (four independent copies): `mounts._NOOP_SHORT_MODES`
+ `_SELINUX_SHORT_MODES`, `compose._NOOP_MOUNT_MODES`, `compose_capabilities.MOUNT_OPTIONS`
+ `MOUNT_SUBKEYS`, and `compose._is_host_path` / `_resolve_host_path` repeating
`mounts.is_host_path` / `_resolve_host_path`.

Related defects:

- **F2a. Late, untyped failure.** Mounts are translated in `_create_service`.
  `mounts.MountSpecError` is a `ValueError`, not a `ShimError`; only `cli.py`
  catches it. A source such as `/tmp/a,b` in Compose produced
  `EXC MountSpecError` (a traceback) after `Creating <name> ...` and the
  network lookups, i.e. after side effects started and, for a later service,
  after earlier services were created.
- **F2b. Accepted but unused attribute.** `consistency` and `bind.create_host_path`
  are `SUPPORTED` in the gate and never read by the translator. `consistency`
  is a documented no-op in Docker outside Docker Desktop; `create_host_path`
  changes behaviour when the host path is missing and needs a live
  classification (task 3.5) before it can stay `SUPPORTED`.
- `ro` / `read_only: true` does reach `--mount ...,readonly` in both syntaxes and
  `m-readonly-volume` proves a failed write live for the short syntax; the long
  syntax and the normalizer path have no live proof.

## F3. Only the last `--env-file` is kept (P2) - CONFIRMED

| Check | Result |
| --- | --- |
| `docker-compose --env-file e1 --env-file e2 config` | both files load, later wins (`B=from_e1`, `C=from_e2`, `D=from_e2`); `.env` is **not** read once `--env-file` is given |
| Same through the shim (`compose.main(["--env-file","e1.env","--env-file","e2.env","config"])`) | `B=unset`, `C=from_e2`: `e1.env` is lost (`main` keeps one `str`) |
| `--env-file=e1.env` and `--project-directory=sub` | `compose: unsupported subcommand: --env-file=e1.env` (exit 64): the `=` spellings are not parsed, although `--file=`, `--profile=` and `--project-name=` are |
| `COMPOSE_ENV_FILES=e1.env,e2.env` | the official binary honours it (the child inherits the environment); the built-in parser ignores it |
| Relative `--env-file` with `--project-directory sub` | resolved against the current directory, not the project directory (official 5.6.0) |
| Missing `--env-file` | official exits 1 with `couldn't find env file: <abs>`; the built-in parser's `_load_env_file` swallows `FileNotFoundError` and continues |
| Empty value | `build_argv` silently omits empty entries, so `--env-file ""` is ignored instead of refused |

`compose_normalizer.Inputs.env_files` and `build_argv` already support several
files and are unit-tested; the loss happens only in `main()`, `_dispatch`,
`_load_project` and `_normalizer_inputs`, which carry a single `str | None`.
`exec --env-file` (`_compose_exec_value_option`) is a different option and is
not touched.

## F4. Cross-cutting review of the path

`compose file -> docker-compose config -> convert_model -> Project -> gate -> translator -> container`

| Stage | What it removes or reinterprets | What orchestration actually needs | Decision |
| --- | --- | --- | --- |
| Normalizer output | prunes unused secrets/networks/volumes, resolves paths to absolute, expands short syntax to long | all of it | keep as is |
| `convert_model` | drops `secrets`, `configs`, `x-*`, services' `None` keys; un-escapes `$$` in services only | `secrets` for build only; `networks`/`volumes` already kept | keep referenced build secrets; un-escape every preserved section; document what stays dropped |
| `Project` | exposes `networks`/`volumes`, not `secrets` | build secrets | add `Project.secrets` |
| Gate | rules for keys it never checks (`build.secrets`, `consistency`, `bind.create_host_path`) say `SUPPORTED`; mounts classified from its own table | one policy shared with the translator | read the `mounts` table; validate build secrets structurally |
| Translator | long syntax ignores unknown nested keys; short syntax has its own mode list | exactly the keys the policy accepts | one pure translation used by both pre-flight and run |
| Execution | mount translation, secret resolution and some existence checks happen after `_ensure_network` / earlier builds | pure checks first | move them into the pre-flight |

Behaviours testable by shared contracts: (1) every top-level key of every
normalized fixture is either in the model or in a documented drop list;
(2) for each mount mode and attribute, the `docker run -v`, Compose and gate
verdicts agree; (3) every secret the gate accepts resolves to an Apple
`--secret` argument or a refusal, never a silent omission.

Out of scope but observed: `bind.create_host_path` (see F2b), Compose `content:`
secrets (rejected by the official schema in 5.6.0).

## F5. Live classification of `bind.create_host_path` and `consistency` (task 3.5)

Observed with `container` 1.5.0 on 2026-10-21, one disposable `shimtest-` run
and the live test `test_live_fixtures.LiveFixtureTests.test_missing_bind_source_is_neither_created_nor_started`:

- `container run --mount type=bind,source=<missing>,target=/d alpine:3.20 true`
  exits non-zero with `Error: path '<missing>' does not exist`. The host path is
  not created and no container is left in `container list --all`.
- Docker creates a missing bind source by default (`create_host_path: true`,
  which the official normalizer emits for every short-syntax bind), so the two
  runtimes differ for a missing source; `create_host_path: false` matches
  Apple's behaviour exactly.
- Decision: `bind.create_host_path` stays **accepted without effect** (it cannot
  be refused: the normalizer emits `true` for ordinary bind mounts). The matrix
  and README must say that a missing bind source fails at start with Apple's
  `path ... does not exist` error instead of being created.
- `consistency` has no Apple counterpart and no effect in Docker Engine outside
  Docker Desktop; it stays accepted without effect.


## F6. Apple `container build` with an unset environment secret (task 4.5)

Observed with `container` 1.5.0 on 2026-10-21 with a throwaway Dockerfile that
reads `/run/secrets/tok` and a disposable `shimtest-qunset-<8hex>:fx` tag:

- `env -u UNSET_TOK_VAR container build --secret id=tok,env=UNSET_TOK_VAR ...`
  exits 64 before any build step with `Error: secret env var doesn't exist
  UNSET_TOK_VAR`; no image is created.
- The same command with `UNSET_TOK_VAR=` (set, empty) builds successfully, so a
  variable that is present with an empty value counts as set, which is what
  `compose_build_secrets.check` assumes.
- Consequence: the pre-flight refusal (exit 64, naming the variable) never
  changes the outcome Apple would give; it only moves the failure before
  `network create`, `volume create` and the other builds of the plan. This
  closes the open question in `design.md`.
