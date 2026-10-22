# release-evidence Specification

## Purpose
Defines the versioned record that a candidate release passed the live tier on the
supported Apple `container` release, how it is produced on a maintainer's machine,
and how the release pipeline refuses a final release that lacks fresh, matching
evidence, without a self-hosted runner.

## Requirements

### Requirement: Evidence is a versioned file
`scripts/validate-runtime.py --evidence` SHALL write one JSON file under
`docs/evidence/` per successful full validation, recording the Apple `container`
version that ran, the combined tree hash, the UTC generation time, the live counts
(ran, passed, failed, skipped, not run) and the result. It SHALL write nothing
when the validation did not fully succeed, when `--skip-live` is also given, or
when the tested directories differ from `HEAD`.

#### Scenario: Successful validation
- **WHEN** the live tier ran every declared test, all passed, and the working tree is clean
- **THEN** a file `docs/evidence/` is written with result `passed`, counts that add up, the runtime version and the tree hash of `HEAD`

#### Scenario: Validation fails
- **WHEN** any live test fails or is not run
- **THEN** no evidence file is written and the exit status is non-zero

#### Scenario: Uncommitted changes
- **WHEN** a tracked file under `src/`, `scripts/` or `tests/` differs from `HEAD`
- **THEN** the command refuses to write evidence and says which directory is dirty

#### Scenario: Read-only mode
- **WHEN** `--evidence` and `--skip-live` are given together
- **THEN** the command exits with a usage error and writes nothing

### Requirement: Evidence is bound to the tested code
The tree hash SHALL be computed from the git tree identifiers of `src/`, `scripts/`
and `tests/` and from the git object identifier of `pyproject.toml`, so that a
commit that changes only documentation, workflows or the evidence files themselves
keeps the evidence valid, and any change to the code, the tests or the packaging
configuration invalidates it.

#### Scenario: Documentation commit
- **WHEN** a commit after the evidence changes only `README.md` and adds the evidence file
- **THEN** the tree hash at the new commit equals the recorded one

#### Scenario: Code commit
- **WHEN** a commit after the evidence changes a file under `src/`
- **THEN** the tree hash differs from the recorded one

#### Scenario: Packaging commit
- **WHEN** a commit after the evidence changes `pyproject.toml`, for example the entry point, the Python floor or the build backend
- **THEN** the tree hash differs from the recorded one and the verifier fails naming both hashes

#### Scenario: Uncommitted packaging change
- **WHEN** `pyproject.toml` differs from `HEAD` and the validation is asked to write evidence
- **THEN** the command refuses and names `pyproject.toml` as the dirty input

### Requirement: Final tags require matching, fresh, complete evidence
A verifier SHALL run in the `checks` job of the release workflow and, for a tag of
the form `vX.Y.Z`, SHALL judge the newest evidence file by its recorded time and
fail unless the runtime version equals `SUPPORTED_CONTAINER_VERSION`, the tree hash
equals the tagged commit's, the evidence is at most 30 days old and not in the
future, and the live tier ran completely and passed. The failure message SHALL name
the first condition that failed.

#### Scenario: Evidence matches
- **WHEN** a final tag points to a commit whose three trees match a passed, 30-day-old or newer evidence for the supported version
- **THEN** the verifier exits 0 and the release proceeds

#### Scenario: Version differs
- **WHEN** the newest evidence names a runtime version other than `SUPPORTED_CONTAINER_VERSION`
- **THEN** the verifier fails naming both versions

#### Scenario: Code changed after the evidence
- **WHEN** `src/`, `scripts/` or `tests/` changed after the evidence was generated
- **THEN** the verifier fails naming both hashes

#### Scenario: Evidence too old
- **WHEN** the newest evidence is older than 30 days
- **THEN** the verifier fails naming its date and the limit

#### Scenario: Live did not run completely
- **WHEN** the evidence records `ran` of zero, a skipped or not-run test, a failed test, or `ran` different from `passed`
- **THEN** the verifier fails naming the counts

#### Scenario: No evidence
- **WHEN** `docs/evidence/` holds no evidence file
- **THEN** the verifier fails and says how to produce one

#### Scenario: Inconsistent file
- **WHEN** the file is malformed JSON, lacks a required field, or its counts do not add up
- **THEN** the verifier fails naming the problem rather than treating it as absent

### Requirement: Pre-release tags do not require evidence
For tags with a PEP 440 pre-release suffix (`rc`, `a`, `b`, `dev`), the verifier SHALL
pass without reading the evidence and SHALL say that live evidence was not
required, and the release SHALL be published as a GitHub pre-release. A tag that
is neither `vX.Y.Z` nor one of those pre-release forms SHALL make the verifier fail.

#### Scenario: Release candidate
- **WHEN** the tag is `v0.4.0rc1`
- **THEN** the verifier exits 0 without evidence and the release is marked pre-release

#### Scenario: Unrecognised tag
- **WHEN** the tag is `v0.4.0-spike`
- **THEN** the verifier fails saying the tag is neither a final release nor a recognised pre-release

### Requirement: Release notes cite the evidence
For a final tag the release notes SHALL include a section naming the evidence file,
the Apple `container` version, the UTC date and the live counts. For a pre-release
the notes SHALL state that live evidence was not required.

#### Scenario: Final notes
- **WHEN** the release job runs for `v0.4.0`
- **THEN** the notes contain the evidence file name, the runtime version, the date and the counts

#### Scenario: Pre-release notes
- **WHEN** the release job runs for `v0.4.0rc1`
- **THEN** the notes contain the sentence that live evidence was not required

### Requirement: Evidence checks need no privileges
The verifier and the workflow steps that call it SHALL run on a hosted runner with
read-only repository permissions, read the tag only from the workflow's tag
variable passed through the environment, and SHALL NOT use a self-hosted runner,
a credential or the network.

#### Scenario: Workflow review
- **WHEN** the release workflow is inspected
- **THEN** the evidence step is in the `checks` job, which keeps `contents: read`, and no step selects a self-hosted runner

### Requirement: The procedure is documented
`docs/releasing.md` SHALL document the sequence for a final release: run the full
validation on a macOS 26 machine with the supported release, commit the evidence
file, then tag; and SHALL state what the evidence does and does not prove,
including that the file is not cryptographically signed.

#### Scenario: Maintainer reads the procedure
- **WHEN** a maintainer follows `docs/releasing.md`
- **THEN** the steps produce a tag the verifier accepts, and the limits of the evidence are stated

### Requirement: Packaging verifier changes invalidate the evidence
The evidence identity covers `src/`, `scripts/`, `tests/` and `pyproject.toml`,
so a change to `scripts/verify_wheel.py` or to any file under `tests/` SHALL make
the committed evidence not match for a final tag, and new evidence SHALL be
produced on the resulting clean tree before tagging `vX.Y.Z`. A change confined
to `.github/workflows/`, `docs/`, `README.md` or `openspec/` SHALL NOT
invalidate it.

#### Scenario: Verifier fix after the evidence
- **WHEN** a commit after the newest evidence file changes `scripts/verify_wheel.py` or `tests/test_verify_wheel.py`
- **THEN** `scripts/verify_release_evidence.py --tag vX.Y.Z` exits non-zero naming the recorded and the current tree hash

#### Scenario: Workflow and documentation commit
- **WHEN** a commit after the evidence changes only `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `docs/releasing.md` or `docs/testing.md`
- **THEN** the tree hash equals the recorded one and the verifier accepts the evidence

#### Scenario: Pre-release
- **WHEN** the tag is `vX.Y.ZrcN` after such a change
- **THEN** the verifier passes without reading the evidence and the release is a pre-release

#### Scenario: Procedure documented
- **WHEN** a maintainer reads `docs/releasing.md`
- **THEN** it lists `scripts/` and `tests/` as inputs whose change requires new evidence, and says the previous file stays in `docs/evidence/` as history while the newest file decides

### Requirement: Packaging checks are not live evidence
The result of `scripts/verify_wheel.py`, of the `wheel` job and of the `smoke`
job SHALL NOT be written to `docs/evidence/`, counted in the `live` counts, or
presented as live validation, and an evidence file SHALL NOT be edited, copied,
renamed or regenerated by hand to match a new tree hash.

#### Scenario: Wheel check passes, evidence stale
- **WHEN** the wheel checks pass and the newest evidence does not match the tagged commit
- **THEN** a final tag is still refused by the evidence verifier

#### Scenario: Evidence files untouched
- **WHEN** this change is implemented
- **THEN** `git diff` shows no modification to an existing file under `docs/evidence/`, and a new file appears only from a successful `validate-runtime.py --evidence` run

#### Scenario: Evidence written only by the live run
- **WHEN** `scripts/verify_wheel.py` runs
- **THEN** it writes nothing under `docs/evidence/` and does not read it

### Requirement: Evidence names the schema it follows
Every evidence file SHALL record a schema number, and the verifier SHALL accept
only the current one. A file written under an earlier schema SHALL fail with a
message that names both numbers and says to run the validation again.

#### Scenario: Current schema
- **WHEN** the newest evidence records the current schema number
- **THEN** the verifier goes on to judge the other conditions

#### Scenario: Earlier schema
- **WHEN** the newest evidence records schema 1 and the current schema is 2
- **THEN** the verifier fails naming both numbers and says the validation must be run again
