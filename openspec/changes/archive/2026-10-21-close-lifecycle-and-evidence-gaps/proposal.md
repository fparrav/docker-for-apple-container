# Proposal

## Why

After `close-compose-compat-gaps` four gaps remain between what the project
claims and what it proves. (1) The shim keeps an anonymous volume across a full
`down` + `up`, while Docker drops it with the container: the conformance test
even carries a comment saying it does not assert this because "the shim keeps
them by design". A Compose project that relies on a fresh `node_modules` or
cache volume after `down` therefore sees stale data, and there is no `up -V` to
force a renewal. (2) `o: rbind` is accepted for local bind volumes, but nothing
verifies that the recursive flag has any effect on Apple's virtiofs mounts or on
Docker Desktop, so the README and matrix claim a behaviour no test shows.
(3) The `conformance` CI job exits 0 when every conformance test is reported
"not run" or skipped, because `scripts/test_report.py` fails only on failed
tests; the job proved 20 tests on Docker 29.4.2 once, and nothing enforces that
it keeps running them. (4) A release can be tagged with no live evidence at all.
The `live` tier needs macOS 26 with the supported Apple `container` release,
which hosted runners cannot provide, and the self-hosted `runtime.yml` has never
run (no runner, no `runtime` environment). The release notes then say
"live tier: not run" for a tool meant for daily use.

## What Changes

- **Anonymous volume lifecycle matches Docker.** An anonymous volume is reused
  only when `up` recreates an existing container of that service. A labelled
  anonymous volume with no container of that service is residue from `down`: it
  is removed and seeded again from the image when the container is created. Add
  `up -V` / `--renew-anon-volumes`, which discards the anonymous volumes of the
  services being recreated and seeds them again. The contract is executable in
  the live and conformance tiers: write a marker, `down`, `up`, the marker is
  gone; recreate keeps it; `up -V` drops it.
- **Reject `o: rbind`** in local bind volumes with exit 64 and a message that
  points to `bind`. The spec, the compatibility matrix, the README and the tests
  stop listing `rbind` as accepted.
- **`scripts/test_report.py --require <tier>`** exits 3 when the required tier
  has any test reported "not run" or skipped, or ran fewer tests than were
  discovered for that tier (no hard-coded numbers). The `conformance` job in
  `ci.yml` uses it. A unit test checks that every fixture whose header says
  `# tiers: ..., conformance` has a conformance test that uses it.
- **Release evidence (option B: versioned evidence, no self-hosted runner).**
  `scripts/validate-runtime.py --evidence` writes a JSON file under
  `docs/evidence/` with the Apple `container` version, a combined hash of the git
  trees of `src/`, `scripts/` and `tests/`, the UTC date, the live counts
  (ran/passed/failed/skipped/not run) and the result. A verifier runs in the
  `checks` job of `release.yml` for final PEP 440 tags (`vX.Y.Z`) only, and fails
  when the version differs from `SUPPORTED_CONTAINER_VERSION`, the hash differs
  from the tagged tree, the evidence is older than the allowed age, or the live
  tier did not run completely. `rc`, `dev` and other pre-release tags are
  published as GitHub pre-releases without requiring evidence, and the notes say
  so. Final release notes cite the evidence.
- **BREAKING (narrow):** `docker compose down` followed by `up` now gives a
  service a fresh anonymous volume (Docker behaviour); data that used to
  survive in an anonymous volume across `down` does not. `o: rbind` volumes that
  used to start are refused with a message naming `bind`.

Out of scope: copy-up for **named** volumes, restoring any self-hosted runner,
running `runtime.yml` automatically, signing evidence cryptographically, and
verifying `rbind` semantics (it is refused instead).

## Capabilities

### New Capabilities
- `release-evidence`: the versioned live-evidence file, how it is produced, and
  the check that gates final releases on it.

### Modified Capabilities
- `compose-anonymous-volumes`: reuse only on recreate, residue removed and
  reseeded, `up -V`, executable contract.
- `compose-resource-compatibility`: `rbind` refused, with the `bind` hint.
- `test-tiers`: a tier can be required, and a required tier that did not fully
  run fails the report.
- `ci-evidence`: the conformance job requires its tier; fixture-to-test
  coverage; the release pipeline's evidence gate.
- `fork-distribution`: final versus pre-release tags, and release notes that
  cite the evidence.

## Impact

- Code: `src/container_docker_shim/compose.py` (`cmd_up`,
  `_ensure_anonymous_volume`, `_parse_flags` for `up`, a residue helper),
  `compose_resource_compat.py` and `compose_capabilities.py` (`rbind`),
  `scripts/test_report.py`, `scripts/validate-runtime.py`, a new
  `scripts/verify_release_evidence.py`.
- Workflows: `ci.yml` (`conformance` job), `release.yml` (`checks` job, the
  `release` job's pre-release flag and notes). No new trigger, permission or
  runner.
- Tests and fixtures: new or changed tests in `tests/test_compose.py`,
  `tests/test_live_fixtures.py`, `tests/test_conformance_fixtures.py`,
  `tests/test_test_report.py`, `tests/test_validate_runtime.py`,
  `tests/test_workflows.py`, `tests/test_compose_resource_compat.py`; the
  `s-anonymous-volume` fixture gains nothing new if the existing one suffices.
- Docs: `README.md`, `docs/testing.md`, `docs/releasing.md`,
  `tests/compat/matrix.json` (and the README block rendered from it), new
  `docs/evidence/`.
- Release process: each final release needs a maintainer to run the live tier
  on a macOS 26 machine and commit the evidence file first.
