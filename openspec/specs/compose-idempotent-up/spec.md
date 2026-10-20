# compose-idempotent-up Specification

## Purpose
Defines how `docker compose up` reconciles existing containers with the Compose
file, so that repeating the command does not destroy containers or data.

## Requirements

### Requirement: Unchanged services are reused
`docker compose up` SHALL keep a service's existing container when its
configuration and image are unchanged, and SHALL start it if it is stopped.

#### Scenario: Repeat up
- **WHEN** the user runs `docker compose up -d` twice with no change in between
- **THEN** the second run creates no container, the container IDs are unchanged and the database process keeps its start time

#### Scenario: Stopped container
- **WHEN** a project container is stopped and `docker compose up -d` runs with an unchanged file
- **THEN** the same container is started and not recreated

### Requirement: Changed services are recreated selectively
`docker compose up` SHALL recreate a service whose configuration or image
changed, together with the services that depend on it, and SHALL leave the
others untouched.

#### Scenario: Environment change
- **WHEN** one service's `environment` changes and `up -d` runs
- **THEN** that service and its dependents are recreated and an unrelated service keeps its container

#### Scenario: Image change
- **WHEN** a service's image is rebuilt or re-tagged to a different ID
- **THEN** that service is recreated on the next `up -d`

### Requirement: Recreation flags
`up` SHALL support `--force-recreate` and `--no-recreate`.

#### Scenario: Force recreate
- **WHEN** the user runs `docker compose up -d --force-recreate`
- **THEN** every selected service is recreated even if unchanged

#### Scenario: No recreate
- **WHEN** the user runs `docker compose up -d --no-recreate` and a service changed
- **THEN** existing containers are kept and the output states that the service differs from the file

### Requirement: State lives in labels
Change detection SHALL use labels on the runtime objects only, and the shim
SHALL NOT keep any file or database of its own.

#### Scenario: No shim state
- **WHEN** `up` completes
- **THEN** the configuration hash and image ID are readable as container labels and no shim-owned file was written

#### Scenario: Container without hash labels
- **WHEN** a project container has no configuration hash label
- **THEN** it is treated as changed and recreated once, with a message saying why

### Requirement: Orphans are never removed implicitly
Containers carrying the project label whose service is no longer in the file
SHALL be reported as orphans and SHALL NOT be removed unless the user passes
`--remove-orphans`.

#### Scenario: Removed service
- **WHEN** a service is deleted from the file and `up -d` runs
- **THEN** the old container is left running and a warning names it

### Requirement: Partial failure is recoverable
When `up` fails while starting a service, it SHALL report the failing service
and leave the project so that a later `up` converges, without removing volumes
or other services' containers.

#### Scenario: Second service fails to start
- **WHEN** the first service starts and the second fails
- **THEN** `up` exits non-zero naming the second service, the first container keeps running, and fixing the file and running `up -d` again starts only what is missing
