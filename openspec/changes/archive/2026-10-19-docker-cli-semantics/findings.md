# Findings

Date: 2026-10-19. Runtime baseline: Docker Desktop Engine 29.9.1 with Docker
Compose v5.6.0; Apple `container` 1.5.0 on arm64 (host build 26B118).

## Spike results

### 1.1: Docker Compose `ps` reference

Captured raw output in `tests/fixtures/compat/ps-reference/`:

- `all.json` and `all.table`: project containing running, exited-with-code-3,
  and created containers.
- `empty.json` and `empty.table`: separate empty project.

`docker compose ps --format json -a` emits one JSON object per line, not a JSON
array. The three rows reported states `created` (exit 0), `exited` (exit 3), and
`running` (exit 0). Each object has 21 fields: 19 strings, `ExitCode` as an
integer, and `Publishers` as an array. Empty-project JSON output is zero bytes;
the default table contains only its header. The fixtures contain no hostnames.

### 1.2: C1 CPU values

Using disposable `shimtest-*` containers on Apple `container` 1.5.0:

| `--cpus` | Result | `nproc` |
|---|---|---:|
| `0.5` | rejected, exit 64 (`invalid`) | — |
| `1.5` | rejected, exit 64 (`invalid`) | — |
| `2` | accepted, exit 0 | 3 |

For D2, refuse fractional CPU values rather than rounding or forwarding them.
Keep integral values unchanged; the observed `nproc` result for `2` was `3`.

### 1.3: C2 env-file and C3 signal spellings

An env file containing `BARE_KEY`, `# COMMENT`, and `SET_KEY=value` was
accepted. The comment was ignored. A bare key inherited its value from the host
environment (`BARE_KEY=inherited-from-host`); when absent from the host it was
omitted from the container environment. `SET_KEY=value` was passed through.

All three `container kill -s` spellings (`TERM`, `SIGTERM`, and `15`) were
accepted and delivered SIGTERM to disposable containers. Each container's
handler wrote `GOT_TERM` to its logs. All signal-test containers were deleted.

### 1.4: Relative bind and `-v name:/t`

`container run --mount type=bind,source=<relative-path>,target=/t` failed with
`path '<relative-path>' does not exist`; it did not create a host directory.
`container run -v <name>:/t` created a named volume automatically and allowed
a write inside it. It did not create a host directory. The disposable volume
was deleted after the observation.

### 1.5: Apple container 1.5.0 exposes no numeric exit code

Verified 2026-10-20 against the supported runtime (Apple `container` 1.5.0,
arm64) with a disposable `shim-ps-probe-3` busybox container that ran
`sh -c 'exit 3'` and was deleted afterwards. `container list --all --format
json` reports the stopped container as `status.state: "stopped"` with no
exit-code field anywhere in the item; `container inspect` returns the same
shape (`configuration.*` plus `id`, `status.startedDate`,
`status.state`). The runtime therefore cannot fill Compose's integer
`ExitCode` or the table's `Exited (N)` code. Consequence: `compose ps
--format json` refuses (exit 64, explanation on standard error, nothing on
standard output) a listing that includes an exited container; the default
table omits the unavailable code; `-q`, `--services`, and `--status`
filtering are unaffected.

## Apply integration results

Date: 2026-10-20. Worktree: Orca worktree `docker-cli-semantics`.

- Default suite: `python3 -m unittest discover -s tests -v` — 814 tests passed,
  49 skipped, 0 failures. The renderer-specific suite passes:
  `python3 -m unittest tests.test_compose_ps -v` — 6 passed.
- Compatibility/docs checks: `PYTHONPATH=tests python3 -m unittest
  tests.test_docs tests.test_compat_matrix_schema tests.test_compat_matrix -v`
  — 43 passed; `python3 scripts/render_compat_table.py --check README.md`
  passed.
- Review-correction checks: R2 focused mounts/CLI suite — 122 passed; R3 named
  volume run cases — 8 passed; R6 mount/create cases — 10 passed. The full
  `tests.test_cli` suite passed (118 tests). Strict OpenSpec validation passed
  after shortening the `compose ps` JSON requirement.
- The direct compatibility/docs module invocation without `PYTHONPATH=tests`
  failed to import `compat_matrix`; rerunning with the repository's `tests/`
  directory on `PYTHONPATH` passed all 43 tests.
- Independent Codex `gpt-6-sol` low-effort review found a `docker create -d`
  volume side-effect gap; R6 deferred named-volume creation until command-level
  validation and added regression coverage.
  `python3 scripts/render_compat_table.py --check README.md` passed.
- Focused live tier: with `SHIM_TIERS=live`, the K short-lived fixture and L
  partial-failure JSON cases passed (2 tests). Their exact `shimtest-fx-*`
  projects were torn down by the fixture harness; read-only container and
  volume listings showed no matching leftovers.
- Configured `SHIM_CONFORMANCE_DOCKER=/usr/local/bin/docker` in the user's
  interactive zsh environment after verifying that this binary uses Docker
  Desktop context `desktop-linux` (Engine 29.9.1). With that configuration,
  `SHIM_TIERS=conformance python3 -m unittest
  tests.test_conformance_fixtures.ConformanceFixtureTests.test_fixture_k_exited_container_reports_numeric_exit_code
  tests.test_conformance_fixtures.ConformanceFixtureTests.test_compose_ps_json_matches_fixture_l_service_states -v`
  passed (2 tests). The full live tier was not run; only the live cases for the
  modified `compose ps` behavior were run in this apply.
- An initial focused live attempt failed because its polling helper parsed the
  old tab-separated format. The helper now requests JSON-lines and the same
  K/L cases pass. No unresolved test failures remain.

