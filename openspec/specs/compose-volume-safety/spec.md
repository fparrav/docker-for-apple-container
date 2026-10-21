# compose-volume-safety Specification

## Purpose
Defines persistence and removal rules for Compose volumes, so that data is
removed only when the user explicitly asks for it and only if the project owns it.

## Requirements

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
`docker compose down -v` SHALL remove only named volumes that the current model
declares as managed by the project, that carry the project's label, and that
were created by the project, and SHALL NOT remove external or explicitly
unlabelled volumes.
#### Scenario: Owned volume
- **WHEN** the user runs `docker compose down -v`
- **THEN** the project's labelled named volumes are removed
#### Scenario: External volume
- **WHEN** a volume is declared `external: true` and the user runs `docker compose down -v`
- **THEN** that volume, which keeps its own name, is not removed and its content is unchanged
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

### Requirement: Read-only mounts are enforced
A mount declared read-only SHALL reject writes inside the container for named
volumes and bind mounts, in short syntax (`:ro`) and long syntax
(`read_only: true`), and the shim SHALL refuse the mount before creating the
container when the runtime cannot enforce it.
#### Scenario: Named volume, long syntax
- **WHEN** a service mounts a named volume with `type: volume` and `read_only: true` and the container tries to write a file there
- **THEN** the write fails and the volume content is unchanged
#### Scenario: Named volume, short syntax through the official normalizer
- **WHEN** a service mounts `data:/data:ro`
- **THEN** a write to `/data` fails exactly as for the long syntax
#### Scenario: Bind mount
- **WHEN** a service mounts `./config:/etc/app:ro`
- **THEN** a write to `/etc/app` fails and the host directory is unchanged
#### Scenario: Runtime cannot enforce it
- **WHEN** Apple `container` has no way to mount a named volume read-only
- **THEN** `up` exits 64 naming the service, the volume and the reason, and no container is created

### Requirement: Mount options are honoured or refused
Mount options without an Apple equivalent (`bind.propagation`,
`bind.create_host_path`, `volume.nocopy`, `volume.subpath`, `tmpfs.size` on
types the runtime lacks, `type: npipe`, unknown flags) SHALL be refused, and
Docker hints with no effect on this platform (`z`, `Z`, `cached`,
`delegated`, `rw`) SHALL be accepted and documented as no-ops.
#### Scenario: Unsupported option
- **WHEN** a service sets `volume: {nocopy: true}` on a mount
- **THEN** `up` exits 64 naming the service, the mount target and the option
#### Scenario: Platform hint
- **WHEN** a service mounts `./src:/src:cached`
- **THEN** the mount is created read-write and no warning is printed
#### Scenario: Unknown flag
- **WHEN** a short-syntax mount carries a flag the shim does not know
- **THEN** `up` exits 64 naming the flag

### Requirement: External volumes keep their name
A volume declared `external: true`, or with an explicit `name:`, SHALL be
mounted under that name, without the project prefix.
#### Scenario: Shared PostgreSQL data
- **WHEN** `db` mounts external volume `shared_data` on its data directory
- **THEN** `container volume list` shows `shared_data` only, and no `<project>_shared_data` exists
