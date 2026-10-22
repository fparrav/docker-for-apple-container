## MODIFIED Requirements

### Requirement: Runtime tier is manual and trusted
The live tier and `scripts/validate-runtime.py` SHALL run only from a maintainer's
macOS 26 machine with virtualization or from a manually triggered workflow on a
self-hosted macOS 26 runner, and SHALL NOT be reachable from pull requests or
forks. A hosted workflow SHALL NOT run the live tier; it MAY verify committed
live evidence (see the `release-evidence` capability).

#### Scenario: Pull request
- **WHEN** a pull request is opened
- **THEN** no job runs the live tier

#### Scenario: Manual run
- **WHEN** a maintainer runs the validation on their machine or triggers the runtime workflow
- **THEN** it runs the live tier against the supported release and produces the per-entry evidence report

#### Scenario: Runtime release mismatch
- **WHEN** the machine has a different Apple `container` release than the supported one
- **THEN** the tier is reported not run with the reason

## ADDED Requirements

### Requirement: The conformance job fails when its tier did not run
The `conformance` job SHALL require its tier, so that it fails when any
conformance test is not run or skipped, when none ran, or when fewer ran than the
test sources declare.

#### Scenario: Silent no-op
- **WHEN** the runner has no reachable Docker Engine and every conformance test is reported not run
- **THEN** the job fails instead of passing with zero executed tests

#### Scenario: Count not written in the workflow
- **WHEN** a conformance test is added or removed
- **THEN** the workflow needs no edit to keep the check meaningful

### Requirement: Every conformance fixture has a test
Each compatibility fixture whose header declares the `conformance` tier SHALL be
exercised by at least one conformance test, and a unit test SHALL fail naming any
fixture that is not.

#### Scenario: New fixture without a test
- **WHEN** a fixture with `# tiers: live, conformance` is added and no conformance test uses it
- **THEN** the unit test fails naming the fixture

#### Scenario: Fixture not in the conformance tier
- **WHEN** a fixture header does not list `conformance`
- **THEN** it is not required to have a conformance test
