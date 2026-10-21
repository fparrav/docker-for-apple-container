# Spec Delta

## Purpose

Defines how tests are separated by what they prove, so that a result always
states whether it covers translation, local execution on Apple `container`, or
compatibility with Docker Engine.

## ADDED Requirements

### Requirement: Four tiers
The test suite SHALL distinguish unit tests, fake-runtime tests, live Apple
`container` tests, and Docker Engine conformance tests, and each test SHALL
belong to exactly one tier.

#### Scenario: Default run
- **WHEN** a developer runs `python3 -m unittest discover -s tests`
- **THEN** only unit and fake-runtime tests run, nothing starts a container, and live and conformance tests are reported as skipped with a reason

#### Scenario: Tier is visible
- **WHEN** a test result is printed
- **THEN** the tier is visible in the test's name or module

### Requirement: Live tier is opt-in and safe
Live tests SHALL run only when explicitly enabled, SHALL use a fixed disposable
name prefix, SHALL clean up in all outcomes, and SHALL NOT use global prune or
touch objects without the prefix.

#### Scenario: Enabled run
- **WHEN** the live tier is enabled and a live test fails
- **THEN** every object created by the test is removed and objects without the prefix are untouched

#### Scenario: Runtime missing
- **WHEN** the live tier is enabled but the apiserver is not running or the release differs from the supported one
- **THEN** the tests are reported as not run with that reason, never as passed

### Requirement: Conformance tier is opt-in and independent
Docker Engine conformance tests SHALL run only on demand or in CI, SHALL require
a real Docker Engine, and SHALL NOT be required for normal use or development.

#### Scenario: No Docker present
- **WHEN** the conformance tier is enabled and no Docker Engine is reachable
- **THEN** the tests are reported as not run

#### Scenario: Same fixture, two runtimes
- **WHEN** a Compose fixture is used in the live and conformance tiers
- **THEN** both tiers use the identical, unmodified file

### Requirement: Shared reproducible fixtures
The suite SHALL provide fixtures for: Nginx alone; API + PostgreSQL; API +
PostgreSQL + Redis; health checks with `depends_on`; networks and volumes; an
ARM64 image; an AMD64 image where the runtime can run it; `.env`
interpolation; multiple Compose files; a container with security restrictions;
a short-lived process; and recovery after a partial failure.

#### Scenario: Fixture inventory
- **WHEN** the fixture directory is listed
- **THEN** each fixture states in a header which tiers use it and which images it needs

#### Scenario: Pinned images
- **WHEN** a fixture references an image
- **THEN** it uses a pinned tag, and the live tier states if the image has to be pulled

### Requirement: Real behaviour is asserted
Live tests SHALL assert observable behaviour (network connection, persisted
data, name resolution, process state, exit code, timeout, cleanup, refusal of
unsupported options), not only that a command exited with 0.

#### Scenario: Exit codes
- **WHEN** a container runs `sh -c 'exit 3'`
- **THEN** the live test asserts that the shim reports exit code 3 for that container

### Requirement: Reproducible procedure for new releases
The project SHALL include a documented, scripted procedure that runs the live
tier and the read-only parse matrix against a candidate Apple `container`
release and records the result.

#### Scenario: Candidate release
- **WHEN** a maintainer runs the procedure against a new release
- **THEN** it reports per matrix entry whether the evidence still holds and writes a summary, without changing the supported-version constant

### Requirement: Honest reporting
No document or report SHALL state that a test passed unless it was executed.

#### Scenario: Skipped tier in a report
- **WHEN** the summary is produced and the live tier did not run
- **THEN** the summary says "live tier: not run"
