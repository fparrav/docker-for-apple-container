## ADDED Requirements

### Requirement: A tier can be required
The test report SHALL accept `--require <tier>` for the opt-in tiers `live` and
`conformance` and SHALL exit with status 3 when that tier did not run completely:
a test reported not run or skipped, no test ran, or fewer passed than the tier's
decorator declares in the test sources. The expected count SHALL be derived from
the sources, never written in the workflow or the script.

#### Scenario: Tier not enabled
- **WHEN** `--require conformance` runs and `SHIM_TIERS` does not enable it
- **THEN** the report lists the tier as not run, stderr names the unmet requirement, and the exit status is 3

#### Scenario: Engine unreachable
- **WHEN** the conformance tier is enabled but no Docker Engine answers
- **THEN** every conformance test is reported not run and the exit status is 3

#### Scenario: A test is skipped
- **WHEN** one required-tier test raises a skip for any reason
- **THEN** the exit status is 3 and stderr names the test and the reason

#### Scenario: A test is missing from the run
- **WHEN** a module of the required tier does not contribute a test that its sources declare with the tier decorator
- **THEN** the exit status is 3 and stderr gives the declared and the passed counts

#### Scenario: Everything ran
- **WHEN** every test of the required tier passed and the counts agree
- **THEN** the exit status is 0

#### Scenario: A test fails
- **WHEN** a required-tier test fails
- **THEN** the exit status is 1 and the failure is listed

#### Scenario: Report still produced
- **WHEN** the requirement is unmet
- **THEN** the JSON report and the summary are written as in any other run

#### Scenario: Unsupported tier
- **WHEN** `--require unit` is given
- **THEN** the report exits with status 2 and a usage message
