## ADDED Requirements

### Requirement: Live tests do not depend on timing
A live test that depends on a service becoming ready SHALL wait on an observable
condition with a bounded timeout and, on timeout, SHALL fail naming the last state
it observed. A fixed delay SHALL NOT decide a verdict. The full live tier on the
supported runtime SHALL give the same result on consecutive runs.

#### Scenario: Slow start
- **WHEN** a fixture service takes longer than usual to become ready but within the timeout
- **THEN** the test passes

#### Scenario: Never ready
- **WHEN** the service does not become ready within the timeout
- **THEN** the test fails and the message names the condition waited for and the last observed state

#### Scenario: Consecutive full runs
- **WHEN** the complete live tier runs three times in a row on `container 1.5.0` with the objects cleaned between runs
- **THEN** every declared live test passes in all three runs and no `shimtest-` object remains
