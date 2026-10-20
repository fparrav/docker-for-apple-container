# Spec Delta

## Purpose

Defines which automated checks run for the fork and where, and how their
results become traceable compatibility evidence, so that no claim of
compatibility is stronger than the checks that were really executed.

## ADDED Requirements

### Requirement: Pull request checks
Every pull request and every push to the default branch SHALL run the unit and
fake tiers, the matrix schema and coverage checks, the README matrix block
check, the documentation consistency tests and a byte-compile of the sources, on
a hosted runner with no secrets.
#### Scenario: Regression in a translation
- **WHEN** a pull request breaks a unit or fake test
- **THEN** the check fails and the report names the test and its tier
#### Scenario: Stale README table
- **WHEN** the matrix changes without regenerating the README block
- **THEN** the check fails naming the differing entries
#### Scenario: Fork pull request
- **WHEN** the pull request comes from another repository
- **THEN** the checks run with read-only permissions and no secrets

### Requirement: Conformance on a real Docker Engine
A CI job SHALL run the conformance tier on a runner that has a Docker Engine,
pointing the tests at the real Docker client, using the same Compose files and
Dockerfiles as the live tier, and its result SHALL be reported separately from
the other tiers.
#### Scenario: Same fixtures
- **WHEN** the conformance job runs
- **THEN** it executes the fixtures under the compatibility fixture directory unmodified
#### Scenario: Engine unavailable
- **WHEN** no Docker client is reachable on the runner
- **THEN** the job reports the tier as not run with the reason and does not report it as passed

### Requirement: Runtime tier is manual and trusted
The live tier and `scripts/validate-runtime.py` SHALL run only from a manually
triggered or default-branch workflow on a self-hosted macOS 26 runner with
virtualization, and SHALL NOT be reachable from pull requests or forks.
#### Scenario: Pull request
- **WHEN** a pull request is opened
- **THEN** no job is scheduled on the self-hosted runner
#### Scenario: Manual run
- **WHEN** a maintainer triggers the runtime workflow
- **THEN** it runs the live tier against the supported release and uploads the per-entry evidence report as an artifact
#### Scenario: Runtime release mismatch
- **WHEN** the runner has a different Apple `container` release than the supported one
- **THEN** the tier is reported not run with the reason

### Requirement: Five outcomes
Every report SHALL classify each test and each matrix entry as exactly one of
passed, failed, skipped, not run, or unsupported, and SHALL define them:
skipped is a test that declined to run for a stated reason, not run is a tier
or test that was never started, unsupported is a documented refusal verified
by a passing test.
#### Scenario: Tier not enabled
- **WHEN** a default run does not enable the live tier
- **THEN** the report lists the live tests as not run and no live test as passed
#### Scenario: Refusal verified
- **WHEN** a matrix entry is UNSUPPORTED and its refusal test passed
- **THEN** the report lists the entry as unsupported, not as passed
#### Scenario: Failure
- **WHEN** any test fails
- **THEN** the report lists it as failed, the summary exit status is non-zero, and no later section hides it

### Requirement: Evidence is traced from the run
For each matrix entry the report SHALL show the claimed evidence level, the
highest level actually produced in this run, and the test ids that produced it,
and SHALL mark entries whose claimed level was not reached.
#### Scenario: Claimed live, not run
- **WHEN** an entry claims `live` and the live tier did not run
- **THEN** the report shows claimed `live`, reached `fake` or lower, and flags the difference
#### Scenario: Claimed conformant
- **WHEN** an entry claims `conformant` and the conformance job passed its test
- **THEN** the report shows the level reached with the test id and the Docker Engine version used

### Requirement: Safe workflows
Workflows SHALL declare minimal permissions, SHALL pin third-party actions to a
full commit identifier, and SHALL NOT use `pull_request_target` to check out
or run pull request code.
#### Scenario: Permissions
- **WHEN** a workflow file is reviewed
- **THEN** it declares `permissions` with read-only repository contents unless the job publishes a release
#### Scenario: Release job
- **WHEN** the release workflow creates a GitHub Release
- **THEN** only that job holds write permission
