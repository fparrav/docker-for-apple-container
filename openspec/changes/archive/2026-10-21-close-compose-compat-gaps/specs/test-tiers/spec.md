# Spec Delta

## ADDED Requirements

### Requirement: Validation does not succeed without live evidence
The runtime validation procedure SHALL exit with a status distinct from success
when the live tier was requested but ran no test, or when a matrix entry claiming
`live` evidence has no executed test, and SHALL exit with success only when every
requested check ran and held. An explicit read-only mode SHALL say in its summary
that the live tier did not run.

#### Scenario: Runtime unavailable
- **WHEN** the live tier is requested and every live test is skipped because the runtime is down
- **THEN** the procedure exits 3, the summary says "live tier: not run", and the CI job that calls it fails

#### Scenario: Entry without an executed test
- **WHEN** the live tier ran but a test named by a `live` matrix entry was not executed
- **THEN** the entry is listed as not run, counts as a problem and the exit status is not 0

#### Scenario: Explicit read-only mode
- **WHEN** the procedure runs with `--skip-live`
- **THEN** it checks the Apple side only, says "live tier: not run (--skip-live)" and exits 0 if no problem was found

#### Scenario: Compound Apple-side commands
- **WHEN** a matrix entry names several Apple commands joined by `;` (for example `container --version; container system status`)
- **THEN** each part is checked on its own and the entry holds only if every part holds

#### Scenario: Everything ran
- **WHEN** all live tests named by `live` entries passed and the Apple flags are present
- **THEN** the procedure exits 0

### Requirement: Conformance assertions do not depend on kernel placeholders
A conformance assertion about an engine's observable behaviour SHALL read data
that reflects that behaviour (such as the interfaces of the container's network
namespace) and SHALL NOT compare against listings that include static kernel
entries which vary by engine host.

#### Scenario: Network mode none
- **WHEN** the `n-network-none` conformance test runs on Docker Desktop
- **THEN** it asserts that the container's network namespace has only the loopback interface, and passes
