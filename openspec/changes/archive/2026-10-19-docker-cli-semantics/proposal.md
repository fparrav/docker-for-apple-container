# Proposal

## Why

The shim answers to Docker's names, so every translated option must mean what
Docker says it means. Three do not. `docker run -c` and `docker build -c` become
`--cpus`, but Docker's `-c` is `--cpu-shares`; `docker run -v data:/d` (a Docker
named volume) becomes a bind mount of a path called `data`; and
`docker compose ps --format json` is documented as supported yet exits 64. In
each case the command looks successful or the documentation says it works, and
an agent has no way to know otherwise. Evidence: `findings.md` items 6.1 to 6.5
and 8.1, 8.2, 10.2 in `compose-capability-gate`.

## What Changes

- Audit every translated `run`, `create` and `build` option for equal meaning
  with Docker (CPU, memory, user, capabilities, network, mounts, signals, process
  limits) and keep the audit as a test-checked table.
- Refuse `-c` / `--cpu-shares` (exit 64, pointing to `--cpus`), refuse
  `--runtime` (Docker selects an OCI runtime, Apple selects a runtime handler),
  and refuse `--network none|host|container:*` explicitly while mapping
  `bridge`/`default` to Apple's default network.
- Translate Docker named volumes in `docker run -v name:/path` and
  `--mount type=volume` to Apple volumes, and translate or refuse Docker's
  `--mount` key aliases instead of forwarding them verbatim.
- Convert units to Docker's meaning (a bare `--memory` number is bytes) or
  refuse what Apple cannot represent; verify fractional `--cpus` live.
- Mark Apple-only options (`--arch`, `--no-dns`, `build --cpus`) as extensions
  in the matrix and README; they are not Docker-compatible claims.
- Implement `docker compose ps` with Docker's table, `--format json`,
  `--status`, `--services`, `-q` and `-a`, and add contract tests for Hermes,
  OpenClaw and generic consumers; correct the README sentence that claimed it.
- Accept the version probes agents run (`docker version --format
  '{{.Server.Version}}'`), which exit 64 today.
- Add a rule: any output format named in README or matrix must be accepted by
  the command and covered by a test, or be listed UNSUPPORTED.
- **BREAKING**: scripts using `docker run -c N` stop working (they were getting
  `--cpus N`, which is not what Docker does); `docker run -v name:/path` starts
  using a real volume instead of failing or binding a relative path.

## Capabilities

### New Capabilities

- `docker-option-semantics`: translated options keep Docker's meaning or are
  refused; Apple-only options are labelled; named volumes, mount dialects,
  network modes and units follow Docker.
- `compose-ps-output`: the formats, filters and fields of `docker compose ps`.

### Modified Capabilities

- `agent-cli-contract`: every documented output format exists and has a
  contract test, including a generic-consumer contract.
- `compatibility-matrix`: options whose Docker and Apple meanings differ are
  recorded with both meanings and the decision.

## Impact

- Code: `cli.py` (`_parse_run_options`, `cmd_build`, `_translate_volume`,
  `_normalize_memory`, `_normalize_cpus`, kill/stop signal handling),
  `compose.py` (`cmd_ps`, the `ps` dispatcher branch).
- Tests: `tests/test_cli.py`, `tests/test_option_sets.py`,
  `tests/test_compat_matrix.py` (the alias table that encodes `-c` -> `--cpus`),
  new `tests/test_option_semantics.py`, `tests/test_compose_ps.py`,
  `tests/test_agent_contract.py`, `tests/test_hermes_contract.py`,
  `tests/test_openclaw_contract.py`, live tests for kill/stop signals, named
  volumes and `--cpus`; matrix entries.
- Docs: README (agent behaviour section, Supported Docker Subset), `docs/testing.md`.
- Compatibility: nothing is added that Docker would reject; Apple-only flags
  keep working. Out of scope: Go-template support beyond the existing bounded
  subset, Engine API, `docker compose ps --format` templates.
- Regression risk: `-c` and `-v name:` are used in scripts; both now have a
  clear message (`-c`) or a correct behaviour (`-v`). Hermes and OpenClaw
  contract tests guard the formats they parse.
- Independent of the other two changes; can land in any order. The matrix
  entries for new Compose keys belong to `compose-capability-gate`.
