# Proposal

## Why

`docker compose` runs on a hand-written YAML subset parser in `compose.py`
(about 300 lines). The README already lists what it cannot read: anchors and
aliases, multi-document streams and `|`/`>` block scalars. It also has no
`-f` merge of several files, no `profiles`, no `extends`, no `include`, and a
partial interpolation. Compose files written by teammates on Linux and Windows
use these features routinely, so a file that Docker accepts can be misread here
instead of rejected, which is the worst outcome for an orchestrator.

The official Docker Compose binary (`docker-compose`, installable with
`brew install docker-compose`, no Docker Desktop) can render the fully merged,
interpolated model as JSON with `config --format json`. If that works without a
Docker daemon, the shim can stop re-implementing Compose parsing and keep only
the part that is specific to Apple `container`: orchestration.

## What Changes

- Add a normalization step that obtains the Compose model from
  `docker-compose config --format json` and feeds it to the existing
  orchestration code as a plain dictionary. The interface between parser and
  runtime is that dictionary, nothing else.
- Resolve the official binary from `CONTAINER_DOCKER_SHIM_COMPOSE` or `PATH`,
  never by invoking `docker compose`, and refuse a binary that resolves back to
  this shim, so the shim cannot call itself.
- When the official binary is absent, keep the built-in parser, but make it
  refuse constructs it cannot read (anchors, aliases, multiline scalars,
  `include`, `extends`, `profiles` without selection) with exit 64, rather than
  misreading them.
- Support `-f` (repeatable), `--env-file`, `--profile`, `-p` and
  `--project-directory` through the normalizer, and `COMPOSE_FILE`,
  `COMPOSE_PROFILES` and `COMPOSE_PROJECT_NAME`.
- Make `docker compose config` and `config -q` use the normalizer's validation
  and output when available.
- Document how to install the official binary without Docker Desktop and which
  mode is active (`docker compose version` reports it).

## Capabilities

### New Capabilities
- `compose-normalization`: how the shim obtains and validates the Compose model
  and what it does when the official normalizer is missing.

### Modified Capabilities

None.

## Impact

- Code: `src/container_docker_shim/compose.py` (loading path and a new
  normalizer adapter; orchestration functions keep their signatures),
  `cli.py` only for flag passthrough of `-f`, `--env-file` and `--profile`.
- Tests: `tests/test_compose.py` gains normalizer-adapter tests with a fake
  `docker-compose`; parser-refusal tests for the built-in path.
- Docs: `README.md` Compose section and install instructions.
- Optional dependency: the official `docker-compose` binary. The shim stays
  pure standard library; the binary is a separate executable, not a package.
- Out of scope: reimplementing Compose features in the shim; supporting
  Compose features that need Docker Engine (`build` with BuildKit-only options,
  `secrets`, `configs`, Swarm `deploy`).
