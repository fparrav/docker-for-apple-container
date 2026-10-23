# compose-resource-identity Specification

## Purpose
Defines how Compose networks and volumes are named, labelled, adopted and
removed, so that the runtime object a container uses is the object the Compose
file describes and cleanup never touches anything the project does not own.

## Requirements

### Requirement: One identity per resource
Each network and volume SHALL resolve to exactly one runtime name: an explicit
`name:` when given, the bare key for `external: true`, and `<project>_<key>`
for managed resources, and every use of the resource SHALL use that name.
#### Scenario: Managed volume
- **WHEN** a project `demo` declares volume `data` without `external` or `name`
- **THEN** the volume is created as `demo_data` and the container mounts `demo_data`
#### Scenario: External volume
- **WHEN** a volume `shared_data` is declared `external: true` and exists
- **THEN** the container mounts `shared_data`, and no volume named `demo_shared_data` is created
#### Scenario: Explicit name
- **WHEN** a managed volume declares `name: pgdata-prod`
- **THEN** the volume is created and mounted as `pgdata-prod` without the project prefix
#### Scenario: Same rules for networks
- **WHEN** a network is declared `external: true` with `name: corp-net`
- **THEN** containers attach to `corp-net` and the shim creates no network for it

### Requirement: External resources must exist and are never changed
Before creating any object, `up` SHALL check that every external volume and
network used by the planned services exists, SHALL fail with exit status 1
naming the resource when one is missing, and SHALL NOT create, relabel,
modify or remove external resources.
#### Scenario: Missing external volume
- **WHEN** a service uses an external volume that does not exist
- **THEN** `up` exits 1 naming the volume and the service, and creates no network, volume or container
#### Scenario: External resource untouched
- **WHEN** `up` and then `down -v` run for a project using an external volume
- **THEN** the volume exists afterwards with its labels and content unchanged
#### Scenario: Runtime failure while checking
- **WHEN** the runtime cannot answer the existence check
- **THEN** `up` exits non-zero with the runtime's message and creates nothing

### Requirement: Managed resources carry ownership labels
Each managed network and volume SHALL be created with the labels
`com.docker.compose.project` and the resource key label Compose uses, and no
other shim-owned record SHALL exist.
#### Scenario: Labels on creation
- **WHEN** `up` creates the managed volume `data` of project `demo`
- **THEN** inspecting it shows project `demo` and key `data`
#### Scenario: No shim state
- **WHEN** the project is torn down and recreated
- **THEN** ownership is read again from labels and no file in the shim's name exists

### Requirement: Removal requires ownership in the model and the label
`down` and `down -v` SHALL remove a network or volume only when the current
model declares it as managed (not external) and the resource carries this
project's label.
#### Scenario: Owned volume
- **WHEN** `down -v` runs for a project with managed volume `data`
- **THEN** `demo_data` is removed
#### Scenario: Declared external now
- **WHEN** a volume was created by an earlier run as managed and the file now declares it `external: true`
- **THEN** `down -v` does not remove it
#### Scenario: Same name in another project
- **WHEN** another project labels a volume `demo_data` and this project resolves the same name
- **THEN** `up` exits 1 stating the resource belongs to another project, and `down -v` never removes it
#### Scenario: Volume removal without a Compose file
- **WHEN** the user runs `docker compose -p demo down -v` where no Compose file can be found
- **THEN** the command exits 64 explaining that volume removal needs the model, and removes nothing, so the user can rerun it with the file or without `-v`
#### Scenario: Unlabelled existing resource
- **WHEN** a managed volume name already exists without a Compose label
- **THEN** `up` reuses it, prints a notice, and `down -v` never removes it

### Requirement: Legacy copies are reported, not deleted
When an external resource has a project-labelled copy named `<project>_<key>`
left by earlier versions, `up` SHALL print a notice naming the copy and SHALL
NOT delete or mount it.
#### Scenario: Stray copy
- **WHEN** `demo_shared_data` exists with the project label and `shared_data` is external
- **THEN** `up` mounts `shared_data`, prints a notice about `demo_shared_data`, and leaves it in place

### Requirement: Data survives and is reused
Data written to a managed volume SHALL be present after `down` and `up`, and
data in an external volume SHALL be present after `down -v`, when the service
is PostgreSQL started with a data directory below the volume mount point.
#### Scenario: Managed volume persistence
- **WHEN** PostgreSQL writes a row, `down` runs, and `up -d` runs again
- **THEN** the row is present
#### Scenario: External volume reuse
- **WHEN** a pre-created volume already contains a row and the project mounts it as external
- **THEN** the service reads that row, and after `down -v` the volume and row still exist

### Requirement: Discovery follows runtime network names
Service discovery between containers SHALL use the runtime names of the
networks they share, including external and explicitly named networks.
#### Scenario: Peers on an external network
- **WHEN** two services share an external network
- **THEN** each resolves the other's service name through the injected hosts entries

### Requirement: The implicit default network exists only when a selected service uses it
`up` SHALL create, check and attach the network with key `default` only when at
least one selected service uses it, either because it declares no network or
because it names `default`. A service that names only other networks SHALL NOT
cause `<project>_default` to be created, checked or reported, and a declared
top-level `default` that no selected service uses SHALL be treated the same way.

#### Scenario: Service without networks
- **WHEN** `up` runs for a project whose only service declares no `networks`
- **THEN** `<project>_default` is created with the project and key labels and the container attaches to it

#### Scenario: Only an external network
- **WHEN** the only service declares `networks: [edge]` and `edge` is `external: true` and exists
- **THEN** the container attaches to `edge` and the runtime receives no `network create` call

#### Scenario: Only a managed explicit network
- **WHEN** the only service declares `networks: [back]` and `back` is managed
- **THEN** `<project>_back` is created, `<project>_default` is not, and the container attaches to `<project>_back`

#### Scenario: Mixed services
- **WHEN** one service declares no networks and another declares `networks: [back]`
- **THEN** both `<project>_default` and `<project>_back` are created, once each

#### Scenario: Explicit reference to default
- **WHEN** a service declares `networks: [default]` or `networks: [default, back]`
- **THEN** `<project>_default` is created and the service attaches to it

#### Scenario: Declared but unused default
- **WHEN** the model declares a top-level `default` network and every selected service uses other networks
- **THEN** `<project>_default` is not created and is not checked

#### Scenario: Unused default belonging to another project
- **WHEN** `<project>_default` exists with another project's label and no selected service uses `default`
- **THEN** `up` succeeds and leaves that network untouched

#### Scenario: Used default belonging to another project
- **WHEN** `<project>_default` exists with another project's label and a selected service uses `default`
- **THEN** `up` exits 1 naming the network and the owner, and creates no network, volume or container

#### Scenario: Missing external network that is needed
- **WHEN** a selected service uses an external network that does not exist
- **THEN** `up` exits 1 naming the network and the service, and creates no network, volume or container, including no `<project>_default`

#### Scenario: Teardown without a default
- **WHEN** `down` runs for a project that never created `<project>_default`
- **THEN** it exits 0 and issues no `network rm` for `<project>_default`

#### Scenario: Teardown of a default created by an earlier version
- **WHEN** `<project>_default` exists with this project's label and the model declares it managed
- **THEN** `down` removes it
