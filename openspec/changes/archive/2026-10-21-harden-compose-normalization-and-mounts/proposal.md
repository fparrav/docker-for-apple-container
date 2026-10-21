# Proposal

## Why

Three defects share one cause: information is lost or reinterpreted between
Docker Compose's normalized model and what the shim finally sends to Apple
`container`, and the capability gate cannot see the loss. With the official
normalizer present (the default path), a valid `build.secrets` project fails
with exit 64, `:Z` / `selinux: Z` volumes are accepted and the relabel request
is silently dropped even though `docker run -v ...:Z` is refused, and repeated
`--env-file` flags keep only the last file. All three were reproduced against
`docker-compose` 5.6.0 and `container` 1.5.0; evidence is in `findings.md`.

## What Changes

- Keep the top-level secret definitions that `build.secrets` needs in the
  normalized model (only those referenced by a build, with `$$` un-escaped),
  resolve each build secret to the BuildKit id Compose uses (`target`, else
  `source`), and check that its file exists or its variable is set before any
  network, volume, image build or container is touched.
- Move the classification of mount modes and long-syntax mount attributes into
  `mounts.py` as the only table. `docker run`, the Compose translator and the
  capability gate all read it. `z`, `Z` and `bind.selinux` are refused
  everywhere; `ro`/`read_only` keep real write protection; `rw`, `cached`,
  `delegated` and `consistency` are accepted as documented no-ops.
- Translate every service's mounts in a pure pre-flight step so an invalid
  mount is a `REFUSED:` line with exit 64 before any resource is created, and
  an uncaught mount error can no longer surface as a traceback.
- Accept `--env-file` any number of times, in order, and also the
  `--env-file=VALUE` and `--project-directory=VALUE` spellings; forward every
  file to the normalizer; make the built-in fallback parser refuse what it
  cannot honour (several env files, `COMPOSE_ENV_FILES`, a missing explicit
  file) instead of guessing.
- Add three shared contract tests that exercise the whole path (normalizer
  output, model, gate, translator) so a future key cannot be accepted by one
  stage and ignored by the next: normalizer-loss, mount-policy agreement and
  secret-resolution.
- Un-escape `$$` in the top-level `networks` and `volumes` the model already
  keeps (labels containing `$` are currently created with `$$`).
- Reclassify `volumes.z`, `volumes.Z` and `bind.selinux` as UNSUPPORTED in the
  compatibility matrix and README, and add live and conformance evidence for
  build secrets, read-only mounts and repeated env files.
- **BREAKING (bug fix)**: Compose files that used `:z` / `:Z` / `selinux:` now
  exit 64 instead of running with the option ignored. The files are not
  modified; the user removes the option (it has no meaning on macOS).

## Capabilities

### New Capabilities

- `compose-build-secrets`: how `build.secrets` is resolved, validated before
  side effects, delivered to the Apple build, and kept out of every output.
- `mount-compatibility`: one classification of Docker mount modes and
  attributes shared by `docker run` and Compose, in short and long syntax, with
  refusal before any resource exists.

### Modified Capabilities

- `compose-normalization`: the model keeps what orchestration reads, repeated
  `--env-file` is preserved in order, and the fallback parser refuses
  environment inputs it cannot honour.

## Out of scope

- Runtime secrets (`services.<name>.secrets`) and `configs`: still refused; they
  need a single-file mount Apple 1.5.0 lacks.
- `docker build --secret` itself (already forwarded verbatim and unchanged).
- `content:` secrets (rejected by Compose 5.6.0's own schema), SELinux waivers,
  `bind.create_host_path` semantics beyond the classification spike in task 3.5,
  and any rewrite of `compose.py`.
- A resident process, Docker Engine as a runtime dependency, or an Engine API
  implementation. Conformance runs remain opt-in and use a client the user has.

## Impact

- Code: `compose_normalizer.py` (`convert_model`), new pure
  `compose_build_secrets.py`, `mounts.py` (policy table and long-syntax parse),
  `compose_capabilities.py` (reads the policy; receives `secrets`), `compose.py`
  (`Project.secrets`, `_validate_plan`, `_classify_and_translate_volume`,
  `_mount_readonly`, `_NOOP_MOUNT_MODES`, `_build_secret_args`, `main`,
  `_dispatch`, `_load_project`, `_normalizer_inputs`,
  `_require_builtin_subset_inputs`), and `cli.py` only where it already calls
  `mounts`.
- Tests and docs: `tests/test_compose_normalizer.py`, `test_mounts.py`,
  `test_compose_capabilities.py`, `test_compose.py`, `test_compose_official.py`,
  `test_live_fixtures.py`, `test_conformance_fixtures.py`, new fixtures under
  `tests/fixtures/compat/`, `tests/compat/matrix.json`, `README.md`,
  `docs/testing.md`.
- No new dependency, no daemon, no change to Apple state handling.
