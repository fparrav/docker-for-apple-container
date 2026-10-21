# Spec Delta

## ADDED Requirements

### Requirement: Isolation requests cannot be waived
A request for network isolation (`docker run --network none`, Compose
`network_mode`) SHALL be refused even when the unenforced-options variable
names it, because skipping it gives the container more connectivity than was
requested.
#### Scenario: Run with no network
- **WHEN** the variable lists `network-none` and the user runs `docker run --network none alpine true`
- **THEN** the command exits 64 and states that isolation requests cannot be waived
#### Scenario: Other waivers keep working
- **WHEN** the variable lists `pids-limit` and the user runs `docker run --pids-limit 100 alpine true`
- **THEN** the option is skipped, a `NOT APPLIED:` line is printed and the command runs

### Requirement: Unenforced options are reported unmistakably
Every option that is skipped or applied only in part, in `docker run`,
`docker create` and Compose, SHALL produce one `NOT APPLIED:` line on standard
error naming the option and the reason, SHALL leave standard output unchanged,
and SHALL keep the exit status of the command.
#### Scenario: Container id stays parseable
- **WHEN** an allowed option is skipped by `docker run -d`
- **THEN** standard output contains only the container id and standard error contains the `NOT APPLIED:` line
#### Scenario: Refusal wording
- **WHEN** an option is refused
- **THEN** standard error starts with `REFUSED:`, the exit code is 64 and the message names the variable that would waive it, unless the option cannot be waived

### Requirement: Waiver names are validated
Names in the unenforced-options variable that match no waivable option SHALL
be reported with a warning on every command, so that a typo cannot create a
false sense of coverage, and only the options documented as waivable SHALL be
accepted.
#### Scenario: Typo
- **WHEN** the variable contains `pid-limit` and the user runs any command
- **THEN** a warning names `pid-limit` as unknown and lists the valid names
#### Scenario: Non-waivable name
- **WHEN** the variable contains `network-none`
- **THEN** the warning says the option cannot be waived
