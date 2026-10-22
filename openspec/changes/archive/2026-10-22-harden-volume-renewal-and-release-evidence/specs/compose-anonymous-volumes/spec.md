## ADDED Requirements

### Requirement: Anonymous volumes are labelled and uniquely named
Each anonymous volume SHALL carry the project label, the service name and its
target, and each new volume SHALL receive a name that differs from the name of
every volume that already exists, so that creating one never replaces, reuses or
changes another volume.

#### Scenario: Two creations of the same declaration
- **WHEN** `down` is followed by `up` for a service with an anonymous volume at `/data`
- **THEN** the second run creates a volume with a different name and the first volume still exists with its labels

#### Scenario: Another project
- **WHEN** two projects have a service named `api` with an anonymous volume at the same target
- **THEN** their volumes are different objects

#### Scenario: Name already taken
- **WHEN** a volume already has the name chosen for a new anonymous volume
- **THEN** the shim chooses another name and the existing volume is not touched

### Requirement: A volume without a container is kept
When `up` creates a container for a service because no container of that service
exists in the project, it SHALL create its anonymous volumes new and from the
image and SHALL NOT remove, modify or mount any anonymous volume that already
exists, whether it was left by `down`, by `docker rm` or by an earlier run.

#### Scenario: Down then up
- **WHEN** a service wrote a marker into its anonymous volume, the user runs `down` and then `up`
- **THEN** the new container does not see the marker, the image content at the target is present, and the previous volume still exists with its marker

#### Scenario: Container removed outside Compose
- **WHEN** the container of the service was removed with `docker rm` and the user runs `up`
- **THEN** the service gets a fresh anonymous volume and the old one is untouched

#### Scenario: Old volume in use elsewhere
- **WHEN** another container still mounts the previous volume
- **THEN** `up` succeeds and does not touch it

#### Scenario: Container kept
- **WHEN** the container of the service is up to date or only stopped
- **THEN** its anonymous volumes are neither created nor seeded again

## MODIFIED Requirements

### Requirement: Recreation keeps anonymous volumes
Recreating an existing container of a service because its configuration or image
changed, or because of `--force-recreate`, SHALL mount into the new container the
anonymous volume that the old container mounted at each target, found from that
container's own mounts and not from the volume's name. A service removed from
the file SHALL leave its anonymous volumes untouched until `down -v`.

#### Scenario: Image change
- **WHEN** the image of a service changes and `up` recreates the container
- **THEN** the same anonymous volume is mounted into the new container

#### Scenario: Forced recreation
- **WHEN** the user runs `up --force-recreate` and the container exists
- **THEN** the anonymous volumes and their data are kept

#### Scenario: Volume made by an earlier version
- **WHEN** the existing container mounts an anonymous volume whose name follows an earlier naming scheme
- **THEN** the recreated container mounts that same volume

#### Scenario: Target added
- **WHEN** the configuration gains an anonymous volume at a target the old container did not mount, and the container is recreated
- **THEN** only that target gets a new volume, created from the image, and the others are reused

### Requirement: Down removes anonymous volumes only with -v
`docker compose down` SHALL NOT remove anonymous volumes. `down -v` SHALL remove
every volume that carries the project label and the anonymous label, including
the volumes that no container mounts any more, and no other volume.

#### Scenario: Plain down
- **WHEN** the user runs `docker compose down`
- **THEN** the project's anonymous volumes still exist

#### Scenario: Down with -v
- **WHEN** the user runs `docker compose down -v`
- **THEN** every labelled anonymous volume of the project is removed, orphans from earlier runs included, and an external, named or foreign volume is not

#### Scenario: Volume still in use
- **WHEN** the runtime refuses to remove one of those volumes because a container still mounts it
- **THEN** the command exits non-zero naming the volume, and the volumes it could remove are removed

#### Scenario: Down -v without the file
- **WHEN** the file cannot be read
- **THEN** `down -v` keeps refusing with exit 64 and removes nothing, as for named volumes

### Requirement: Renewing anonymous volumes with -V
`docker compose up` SHALL accept `-V` and `--renew-anon-volumes`. For every
service whose container is recreated by that run, they SHALL mount new anonymous
volumes created from the image instead of the ones the old container mounted, and
SHALL leave the previous volumes in place. They SHALL NOT affect a container that
is kept or only started, and SHALL NOT affect named volumes.

#### Scenario: Recreate with -V
- **WHEN** a service holds a marker in its anonymous volume and the user runs `up -V --force-recreate`
- **THEN** the new container does not see the marker, the image content is present, and the previous volume still exists with the marker

#### Scenario: Recreate without -V
- **WHEN** the same run omits `-V`
- **THEN** the marker is still there in the same volume

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
by `up` each drop it while the previous volume still exists, and `down -v` removes
the volume of the current container.

#### Scenario: Same sequence on both engines
- **WHEN** the live and conformance tests for the anonymous volume fixture run
- **THEN** both assert the same outcome after recreate, after `-V`, after `down` and `up`, and after `down -v`

#### Scenario: Orphans removed by the shim only
- **WHEN** the live test runs `down -v` after two earlier recreations left orphans
- **THEN** it asserts every labelled volume of the project is gone, and the conformance test does not assert this because Docker keeps them

## REMOVED Requirements

### Requirement: Anonymous volumes are labelled and named deterministically
**Reason**: A name derived from the project, service and target forces the next `up` to reuse or delete the previous volume. Docker gives each anonymous volume a new identity and leaves the old one.
**Migration**: Volumes created under the old name keep working: a container that mounts one reuses it on recreation, and an unused one is an ordinary labelled orphan removed by `down -v`.

### Requirement: Volumes without a container are residue
**Reason**: Deleting a volume that `down` left behind destroys data that Docker only orphans, and the user could not recover it.
**Migration**: Replaced by "A volume without a container is kept". The leftover volume is removed with `down -v` or `docker volume rm`.
