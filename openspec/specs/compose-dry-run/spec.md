# compose-dry-run Specification

## Purpose
Lets a user find out, without side effects, whether a Compose project would be
accepted by the shim, so migrating off Docker Desktop can be checked beforehand.

## Requirements

### Requirement: Up can be evaluated without side effects
`docker compose --dry-run up [SERVICE...]` SHALL load the project, run the
capability gate, the mount and build-secret pre-flight and the read-only resource
checks, print what `up` would do, and SHALL NOT create, start, build, pull, stop or
remove anything.

#### Scenario: Accepted project
- **WHEN** the project passes every check
- **THEN** the command prints the networks, volumes and services it would create, in dependency order, and exits 0

#### Scenario: Nothing is created
- **WHEN** the command runs with a recording runtime
- **THEN** no call creates, runs, builds, pulls, starts, stops or removes any object

#### Scenario: Refused project
- **WHEN** the gate refuses a key
- **THEN** the same `REFUSED:` lines as a real `up` are printed and the exit code is 64

### Requirement: Dry run reports the same problems as up
Every problem that `up` reports before its first side effect SHALL be reported
by the dry run with the same text and exit code, including resource conflicts
and missing build secrets for services that would build.

#### Scenario: Name conflict
- **WHEN** a managed volume name exists under another project's label
- **THEN** the dry run exits 1 with the same message as `up`

### Requirement: Dry run is limited to up
`--dry-run` given to any other command SHALL exit 64 saying it is only supported
with `up`, and SHALL NOT run that command.

#### Scenario: Down
- **WHEN** the user runs `docker compose --dry-run down`
- **THEN** the command exits 64 and removes nothing

### Requirement: Dry run output is not a promise of success
The dry run SHALL state that image availability, build results and start-up
health are not evaluated.

#### Scenario: Footer
- **WHEN** the dry run exits 0
- **THEN** the last output line says that only configuration was checked

### Requirement: Dry run lists the networks that up would create and check
The networks printed by `docker compose --dry-run up` SHALL be exactly the set
that a real `up` of the same selection would check and create, and the dry run
SHALL NOT create any of them.

#### Scenario: Explicit networks only
- **WHEN** the dry run runs for a project whose services use only `edge` (external) and `back` (managed)
- **THEN** the output lists `edge (external, must exist)` and `<project>_back`, does not list `<project>_default`, and no call creates anything

#### Scenario: Default in use
- **WHEN** the dry run runs for a project where one service declares no networks
- **THEN** the output lists `<project>_default` once

#### Scenario: Missing external network
- **WHEN** a used external network does not exist
- **THEN** the dry run prints the same message as a real `up` and exits 1 before printing the plan

#### Scenario: Unused default conflict is not reported
- **WHEN** `<project>_default` belongs to another project and no selected service uses `default`
- **THEN** the dry run exits 0 and prints no conflict for it
