# compose-anonymous-volumes Specification

## Purpose
Defines how Compose volumes declared without a source (`- /app/node_modules`)
are created, reused, populated and removed on a runtime that creates empty
volumes and leaves its own anonymous volumes behind.

## Requirements

### Requirement: Anonymous volumes are supported
A service volume entry with no source, in short or long syntax, SHALL be mounted
at its target on a volume that belongs to the project and the service, instead of
being refused.

#### Scenario: Short syntax over a bind mount
- **WHEN** a service mounts `./src:/app` and `/app/node_modules`
- **THEN** `up` succeeds and `/app/node_modules` is a separate volume that is not the host directory

#### Scenario: Long syntax
- **WHEN** a service lists `{type: volume, target: /cache}` without a source
- **THEN** it is treated the same as `- /cache`

#### Scenario: Two targets
- **WHEN** a service has two anonymous volumes with different targets
- **THEN** each gets its own volume

### Requirement: Anonymous volumes are labelled and named deterministically
Each anonymous volume SHALL carry the project label, the service name and its
target, and its name SHALL be derived from the project, service and target so that
the same declaration always resolves to the same volume.

#### Scenario: Same declaration, same volume
- **WHEN** `up` runs twice on an unchanged file
- **THEN** the second run reuses the volume created by the first

#### Scenario: Another project
- **WHEN** two projects have a service named `api` with an anonymous volume at the same target
- **THEN** their volumes are different objects

### Requirement: Image content is copied on first use
When an anonymous volume is created by a run, the content that the service's image
has at the target path SHALL be copied into it before the container starts, as
Docker does, and SHALL NOT be copied again into a volume that already existed.

#### Scenario: Installed dependencies are visible
- **WHEN** the image contains files under `/app/node_modules` and the service mounts `./src:/app` plus `/app/node_modules`
- **THEN** the files from the image are present in the container at `/app/node_modules`

#### Scenario: Path absent from the image
- **WHEN** the target does not exist in the image
- **THEN** the volume starts empty and no error is reported

#### Scenario: Existing volume keeps its data
- **WHEN** the service is recreated and the volume already exists
- **THEN** the data written to the volume is still there and the image content is not copied over it

#### Scenario: Content cannot be copied
- **WHEN** the image has no shell or no copy tool and the target exists in the image, or the copy fails
- **THEN** the new volume is removed, the command exits non-zero naming the service, the image and the target, and the service is not started

### Requirement: Recreation keeps anonymous volumes
Recreating a service because its configuration or image changed SHALL keep and
reuse its anonymous volumes, and a service removed from the file SHALL leave its
anonymous volumes untouched until `down -v`.

#### Scenario: Image change
- **WHEN** the image of a service changes and `up` recreates the container
- **THEN** the same anonymous volume is mounted into the new container

### Requirement: Down removes anonymous volumes only with -v
`docker compose down` SHALL NOT remove anonymous volumes, and `down -v` SHALL
remove the anonymous volumes of the project's services that carry the project
label, and no other volume.

#### Scenario: Plain down
- **WHEN** the user runs `docker compose down`
- **THEN** the project's anonymous volumes still exist

#### Scenario: Down with -v
- **WHEN** the user runs `docker compose down -v`
- **THEN** the project's labelled anonymous volumes are removed and an external or foreign volume is not

#### Scenario: Down -v without the file
- **WHEN** the file cannot be read
- **THEN** `down -v` keeps refusing with exit 64 and removes nothing, as for named volumes

### Requirement: No orphan from the runtime's own anonymous volumes
The shim SHALL NOT use the runtime's own anonymous volume feature for Compose
services, so that removing a container never leaves an unlabelled volume behind.

#### Scenario: Containers removed
- **WHEN** a project with anonymous volumes is brought down without `-v`
- **THEN** every remaining volume is labelled with the project

### Requirement: Mount options on anonymous volumes
An anonymous volume SHALL accept the same mount options as a named volume under
the mount policy, and a refused option SHALL be refused before any resource is
created.

#### Scenario: Refused option
- **WHEN** an anonymous volume entry sets `volume.subpath`
- **THEN** `up` exits 64 with a `REFUSED:` line and creates nothing
