# Spec Delta

## Purpose

Defines persistence and removal rules for Compose volumes, so that data is
removed only when the user explicitly asks for it and only if the project owns it.

## ADDED Requirements

### Requirement: Named volumes persist
Named volumes declared by a project SHALL persist data across `stop`, `down`,
`up` and recreation of the container that uses them.

#### Scenario: PostgreSQL data survives down
- **WHEN** a PostgreSQL service writes a row, `docker compose down` runs, and `docker compose up -d` runs again
- **THEN** the row is still present

#### Scenario: Recreation keeps data
- **WHEN** a service is recreated because its configuration changed
- **THEN** its named volumes are reused with their data

### Requirement: Down does not delete volumes by default
`docker compose down` SHALL remove containers and the project network and SHALL
NOT remove any volume.

#### Scenario: Down without flags
- **WHEN** the user runs `docker compose down`
- **THEN** the project's named volumes still exist afterwards

### Requirement: Down -v removes only owned volumes
`docker compose down -v` SHALL remove only named volumes that carry the
project's label and were created by the project.

#### Scenario: Owned volume
- **WHEN** the user runs `docker compose down -v`
- **THEN** the project's labelled named volumes are removed

#### Scenario: External volume
- **WHEN** a volume is declared `external: true` and the user runs `docker compose down -v`
- **THEN** that volume is not removed

#### Scenario: Volume of another project
- **WHEN** another project has a volume with a similar name and `down -v` runs for the first project
- **THEN** the other project's volume is not removed

### Requirement: Bind mounts
Bind mounts SHALL resolve relative to the Compose project directory, SHALL
never be created or deleted by the shim, and SHALL preserve file contents
written by the container.

#### Scenario: Relative bind
- **WHEN** a service mounts `./data:/data` and writes a file
- **THEN** the file appears in the project's `data` directory on the host

### Requirement: Shared volumes
A named volume used by several services SHALL be mounted into each of them.

#### Scenario: Two services share a volume
- **WHEN** two services mount the same named volume
- **THEN** a file written by one is readable by the other

### Requirement: Permissions are reported
Where the runtime does not map file ownership as Docker does, the documentation
SHALL state the observed behaviour for bind mounts and named volumes.

#### Scenario: Ownership of written files
- **WHEN** a container running as UID 1000 writes into a bind mount
- **THEN** the documentation records the UID and mode observed on the host in the live tests
