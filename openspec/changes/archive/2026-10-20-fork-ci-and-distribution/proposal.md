# Proposal

## Why

The fork has a four-tier test suite and a compatibility matrix, but nothing runs
them automatically: the only workflow publishes to PyPI under the upstream
project's name, `pyproject.toml`, the README badges and install commands still
point to upstream, `docker version` does not say which shim version is
installed, and the committed `AGENTS.md` still names Apple `container` 1.4.1
while the code requires 1.5.0 (`compose-capability-gate/findings.md` 10.1, 10.3,
11.1, 11.2, 12.1). A reader cannot install the fork unambiguously, confirm what
they installed, or see which compatibility claims were actually exercised.

## What Changes

- Add pull-request CI for the tiers that run on hosted runners: unit, fake,
  matrix and documentation consistency, README block check, byte-compile; plus a
  conformance job on a runner that has Docker, using the same fixtures.
- Define the runtime tier as a manually triggered job for a self-hosted macOS 26
  runner with virtualization, never reachable from fork pull requests.
- Report outcomes with five states (passed, failed, skipped, not run,
  unsupported) and trace every matrix claim to the highest evidence actually
  produced in the run.
- Make the fork installable and identifiable on its own: a distribution name
  that does not collide with upstream, install from the fork's Git tag with
  `uv` or `pipx`, from source, `docker version` showing the shim and runtime
  versions, semantic versioning through tags, GitHub Releases as the default
  channel, PyPI publishing off unless the owner enables it.
- Remove upstream coupling: badges, tap and website links, authors and URLs in
  metadata, the PyPI workflow, the "refresh the central website" duty. Keep the
  MIT licence and credit upstream.
- Extend the documentation consistency test to every document that states the
  supported runtime, including agent guidance when present, so the 1.4.1/1.5.0
  contradiction cannot recur.

## Capabilities

### New Capabilities

- `ci-evidence`: which checks run at which level, on which runners, with which
  trust boundary, and how results and compatibility evidence are reported.
- `fork-distribution`: how the fork is named, installed, versioned, released
  and identified, and how it avoids depending on upstream infrastructure.

### Modified Capabilities

- `runtime-compatibility`: documentation consistency covers all documents that
  state the supported runtime, and `docker version` identifies the shim.
- `test-tiers`: reports use five outcomes and a "not run" tier is never
  folded into "skipped".

## Impact

- New files: `.github/workflows/ci.yml`, `.github/workflows/runtime.yml`,
  `scripts/test_report.py`; changed: `.github/workflows/release.yml`,
  `pyproject.toml`, `README.md`, `docs/testing.md`, `docs/releasing.md` (new),
  `src/container_docker_shim/cli.py` (`cmd_version`), `tests/test_docs.py`,
  `tests/test_cli.py`.
- No runtime behaviour of translations changes. `docker version` gains one
  line, which is covered by the Hermes contract test (it only checks the
  container line).
- Compatibility: the executable stays `docker`; the Python import package stays
  `container_docker_shim`. Users who installed upstream's package are told how to
  switch, nothing is migrated automatically.
- Out of scope: publishing to PyPI or a Homebrew tap now (the design records the
  conditions), modifying upstream's repository, packages or website, and
  changing `AGENTS.md` (the owner's file; the change only adds a test that
  catches a stale version literal and states what the owner needs to commit).
- Risks: self-hosted runners are a supply-chain risk on public repositories;
  mitigated by manual triggers, protected environment and no fork PR access.
- Independent of the three Compose/CLI changes; its test report script consumes
  their new tests as they land.
