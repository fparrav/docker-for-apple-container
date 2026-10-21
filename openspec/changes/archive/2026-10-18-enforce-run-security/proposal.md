# Proposal

## Why

`docker run` and `docker compose up` accept several security and resource
restrictions that Apple `container` 1.5.0 cannot honour, and the shim drops
them without failing. `--security-opt`, `--pids-limit` and `--storage-opt` are
accepted as silent no-ops (`cli.py`, `run` translation), and Compose keys such
as `privileged`, `sysctls`, `devices` and `security_opt` only print a one-line
warning (`UNSUPPORTED_SERVICE_KEYS` in `compose.py`). A container that was
asked to run with `--security-opt no-new-privileges` or `--pids-limit 100`
therefore starts with fewer restrictions than requested, and an agent reading
the exit code 0 has no way to know.

The options that Apple does support (`--cap-add`, `--cap-drop`, `--read-only`,
`--ulimit`, `--tmpfs`, `--user`, `--memory`, `--cpus`) are forwarded, but nothing
verifies that the requested value reaches the container. `--tmpfs` discards its
options (`ro`, `noexec`, `size=`), and `--cap-add` forwards Docker-style names
(`NET_ADMIN`) while the runtime documents the `CAP_NET_RAW` form.

The maintainer's rule is that a security restriction is never ignored
silently: if it cannot be applied, the run fails before the container exists.

## What Changes

- Classify every security-relevant `run`/`create` option and Compose service
  key as SUPPORTED, PARTIAL or UNSUPPORTED in one table in the code, used by
  both the CLI and Compose translators.
- UNSUPPORTED options fail with exit 64 and a message naming the option and the
  reason, before any `container run` or `container create` is issued, so no
  container is left behind.
- SUPPORTED options are verified end to end: after creation, the applied value
  is readable from `docker inspect` and a live test proves the effect.
- PARTIAL options (for example `--tmpfs` with only a path) apply what Apple
  supports and refuse the parts that carry a restriction, instead of
  discarding them.
- An explicit, per-option opt-in lets the user run third-party Compose files
  that carry hardening keys the runtime cannot enforce (see design). The
  default stays "refuse".
- Normalize Docker capability names (`NET_ADMIN`, `ALL`) to the form the 1.5.0
  runtime accepts, or refuse them with a clear message when it cannot.
- Remove the "silent no-op" caveat from the README and replace it with the
  classification table.

## Capabilities

### New Capabilities
- `run-security-options`: how security and resource options of `docker run`,
  `docker create` and Compose services are classified, enforced, verified and
  refused.

### Modified Capabilities

None. `openspec/specs/` has no capability for these options yet.

## Impact

- Code: `src/container_docker_shim/cli.py` (run/create flag translation),
  `src/container_docker_shim/compose.py` (service-key handling and run-arg
  building), a small shared classification table.
- Tests: `tests/test_cli.py`, `tests/test_compose.py`, new live-test cases for
  capabilities, read-only root and user.
- Docs: `README.md` caveats section replaced by the classification table.
- Behaviour change: commands that used to succeed with ignored options now exit
  64. This is intended and is called out in the README.
- Out of scope: emulating missing controls inside the guest (for example
  applying a PID limit with `ulimit` from an entrypoint wrapper).
