# Spec Delta

## ADDED Requirements

### Requirement: Reports use five outcomes
The test report SHALL distinguish passed, failed, skipped, not run and
unsupported, and a tier that was not enabled SHALL be reported as not run for
all of its tests, never as skipped or passed.
#### Scenario: Default run
- **WHEN** the default run completes
- **THEN** the report lists the live and conformance tiers as not run with the reason and gives counts for passed, failed and skipped tests of the tiers that ran
#### Scenario: Enabled tier with a missing runtime
- **WHEN** the live tier is enabled and the apiserver is down
- **THEN** its tests are reported as not run with the reason, not as skipped or passed
