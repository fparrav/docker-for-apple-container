# Design

## Context

The suite has 125 tests in unittest style. They use a fake `container` script
that returns canned JSON, so they prove translation only. `test_docs.py`
already checks that the README mentions the supported version. `test_hermes_contract.py`
and `test_openclaw_contract.py` pin some consumer behaviour. Live checks are
manual and recorded in `findings.md` files inside OpenSpec changes. The
repository rule is to use read-only live checks or explicitly scoped disposable
containers, never global prune. The runtime is Apple `container` 1.5.0 on macOS
with virtualization, which hosted CI runners generally lack.

## Goals / Non-Goals

**Goals:**
- A claim and its evidence level are stored together and checked.
- Opt-in live and conformance tiers that can be run with one command.
- Verified facts about multi-arch builds and ECR instead of assumptions.

**Non-Goals:**
- Making live tests run on hosted CI. The limitation is documented.
- A new test framework; unittest stays, so there is no new dependency.
- Docker Engine as a runtime dependency of any kind.

## Decisions

**One JSON matrix, derived views.** `tests/compat/matrix.json` holds entries
`{id, kind, status, apple, evidence, test, note}`. A unit test loads it and
checks: every id appears in the README table, every `live` entry names an
existing test, and every option recognized by the run/build/Compose
translators has an entry (the translators expose their option sets). The
README table is rendered by a small script and verified by the same test.
Alternative: YAML; rejected because the project has no YAML dependency.
Alternative: prose only; rejected because it is what drifts today.

**Tiers by environment variable and module name.** Tests live in
`tests/unit_*`, `tests/fake_*`, `tests/live/test_*` and `tests/conformance/test_*`
(existing files keep their names and are tagged by a decorator rather than
moved, to keep the diff reviewable). `SHIM_TIERS=live` and `SHIM_TIERS=conformance`
enable the opt-in tiers through a shared `skipUnless` helper. The helper also
checks `container system status` and the supported version, and the skip
message states the reason. Alternative: pytest markers; rejected because the
project runs plain unittest and avoids third-party packages.

**Disposable names.** Live tests create objects named `shimtest-<run id>-...`
and clean up in `addCleanup`. The helper's cleanup lists objects by that prefix
and by labels only. It never calls any prune.

**Fixtures are plain, standard files.** Fixtures A to L are ordinary Compose
files and Dockerfiles under `tests/fixtures/`. A header comment lists tiers and
images. The same file feeds the live tier and the conformance tier, which is
the point of the conformance tier. Images are pinned (for example
`postgres:17`, `nginx:1.27-alpine`, `redis:7-alpine`, `alpine:3.20`).

**Agent contract tests are explicit.** The consumer contract tests enumerate
the commands and shapes each consumer uses. New tests cover the spec: minimal
environment, exit-code propagation, stderr/stdout separation, and signal
propagation (a subprocess test that sends SIGTERM to an attached run and checks
there is no surviving child). Signal propagation touches how the shim spawns
`container`; if the shim uses `subprocess.run` the child may survive a signal,
and the fix is in scope here.

**Image workflow spikes decide documentation.** Before writing claims, probe
live: `container build --platform linux/amd64` (does the builder honour it,
does Rosetta run it), `uname -m` in the result, `--platform linux/amd64,linux/arm64`,
the push result for a built tag (single manifest or index, read from a local
registry's manifest endpoint), and `container registry login` with an ECR-style
username/password pair against a local registry. Unsupported or unverified
behaviour is refused or documented as such. `--secret` and `--ssh` exist in
`container build` 1.5.0 and are candidates to translate; `--iidfile` and
`--push` stay refused unless a faithful mapping exists.

**Overhead measurement.** A script runs `container ls` and the equivalent
`docker ps` ten times each and records median and maximum wall-clock time and
the shim's peak resident memory. Numbers go into the findings; no optimization
is made without a measured cost.

**Release validation procedure.** `scripts/validate-runtime.sh` (or a Python
equivalent) runs the read-only matrix, then the live tier, then prints a table
of matrix entries and the evidence that held. It never edits the supported
version.

## Risks / Trade-offs

- [Matrix maintenance burden] → The check is automatic; adding an option
  without an entry fails the suite, which is the intended friction.
- [Live tests are slow and need image pulls] → Opt-in, pinned small images, and
  the procedure documents expected time and disk use.
- [Conformance tier needs Docker on another machine] → It is designed to run in
  CI or on demand; its absence reports "not run".
- [Signal handling change affects interactive use] → Covered by the existing
  interactive tests plus the new subprocess test before the change lands.
