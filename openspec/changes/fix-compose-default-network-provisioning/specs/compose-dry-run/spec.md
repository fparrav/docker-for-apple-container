## ADDED Requirements

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
