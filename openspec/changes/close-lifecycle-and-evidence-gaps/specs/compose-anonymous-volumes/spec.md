## MODIFIED Requirements

### Requirement: Image content is copied on first use
When an anonymous volume is created by a run, the content that the service's image
has at the target path SHALL be copied into it before the container starts, as
Docker does, and SHALL NOT be copied again into a volume that is reused because the
service's existing container is being recreated.

#### Scenario: Installed dependencies are visible
- **WHEN** the image contains files under `/app/node_modules` and the service mounts `./src:/app` plus `/app/node_modules`
- **THEN** the files from the image are present in the container at `/app/node_modules`

#### Scenario: Path absent from the image
- **WHEN** the target does not exist in the image
- **THEN** the volume starts empty and no error is reported

#### Scenario: Existing volume keeps its data
- **WHEN** an existing container of the service is recreated and its anonymous volume exists
- **THEN** the data written to the volume is still there and the image content is not copied over it

#### Scenario: Content cannot be copied
- **WHEN** the image has no shell or no copy tool and the target exists in the image, or the copy fails
- **THEN** the new volume is removed, the command exits non-zero naming the service, the image and the target, and the service is not started

### Requirement: Recreation keeps anonymous volumes
Recreating an existing container of a service because its configuration or image
changed, or because of `--force-recreate`, SHALL keep and reuse its anonymous
volumes. A service removed from the file SHALL leave its anonymous volumes
untouched until `down -v`.

#### Scenario: Image change
- **WHEN** the image of a service changes and `up` recreates the container
- **THEN** the same anonymous volume is mounted into the new container

#### Scenario: Forced recreation
- **WHEN** the user runs `up --force-recreate` and the container exists
- **THEN** the anonymous volumes and their data are kept

### Requirement: Down removes anonymous volumes only with -v
`docker compose down` SHALL NOT remove anonymous volumes, and `down -v` SHALL
remove the anonymous volumes of the project's services that carry the project
label, and no other volume. The volumes a plain `down` leaves are residue that
the next `up` discards (see "Volumes without a container are residue").

#### Scenario: Plain down
- **WHEN** the user runs `docker compose down`
- **THEN** the project's anonymous volumes still exist until the next `up` or `down -v`

#### Scenario: Down with -v
- **WHEN** the user runs `docker compose down -v`
- **THEN** the project's labelled anonymous volumes are removed and an external or foreign volume is not

#### Scenario: Down -v without the file
- **WHEN** the file cannot be read
- **THEN** `down -v` keeps refusing with exit 64 and removes nothing, as for named volumes

## ADDED Requirements

### Requirement: Volumes without a container are residue
When `up` creates a container for a service because no container of that service
exists in the project, any anonymous volume of that service that already exists
SHALL be removed and created again from the image, so that `down` followed by `up`
gives the service a fresh volume, as Docker does. Only a volume that carries the
project, service and anonymous labels SHALL be removed.

#### Scenario: Down then up
- **WHEN** a service wrote a marker into its anonymous volume, the user runs `down` and then `up`
- **THEN** the marker is gone and the image content at the target is present again

#### Scenario: Container removed outside Compose
- **WHEN** the container of the service was removed with `docker rm` and the user runs `up`
- **THEN** the service gets a fresh anonymous volume

#### Scenario: Volume in use
- **WHEN** the runtime refuses to remove the residue volume because something still uses it
- **THEN** the command exits non-zero naming the volume and the service, and the service is not started

#### Scenario: Unlabelled volume with the same name
- **WHEN** a volume with the deterministic name exists without the project, service and anonymous labels
- **THEN** `up` exits non-zero naming the volume and removes nothing

#### Scenario: Container kept
- **WHEN** the container of the service is up to date or only stopped
- **THEN** its anonymous volumes are neither removed nor seeded again

### Requirement: Renewing anonymous volumes with -V
`docker compose up` SHALL accept `-V` and `--renew-anon-volumes`. For every service
whose container is recreated by that run, they SHALL discard its anonymous volumes
and create them again from the image instead of reusing them. They SHALL NOT
affect a container that is kept or only started, and SHALL NOT affect named volumes.

#### Scenario: Recreate with -V
- **WHEN** a service holds a marker in its anonymous volume and the user runs `up -V --force-recreate`
- **THEN** the marker is gone and the image content is present

#### Scenario: Recreate without -V
- **WHEN** the same run omits `-V`
- **THEN** the marker is still there

#### Scenario: Container kept with -V
- **WHEN** the container is up to date and the user runs `up -V`
- **THEN** the container and its volumes are untouched

#### Scenario: Named volumes
- **WHEN** a service also mounts a named volume and the user runs `up -V --force-recreate`
- **THEN** the named volume and its data are kept

#### Scenario: Dry run
- **WHEN** the user runs `docker compose --dry-run up -V`
- **THEN** nothing is created or removed

### Requirement: Lifecycle is proven against Docker and the runtime
The anonymous volume lifecycle SHALL be asserted by one test in the live tier and
one in the conformance tier that run the same sequence on the same fixture: write
a marker, `up --force-recreate` keeps it, `up -V --force-recreate` and `down` followed
by `up` each drop it.

#### Scenario: Same sequence on both engines
- **WHEN** the live and conformance tests for the anonymous volume fixture run
- **THEN** both assert the same outcome after recreate, after `-V`, and after `down` and `up`
