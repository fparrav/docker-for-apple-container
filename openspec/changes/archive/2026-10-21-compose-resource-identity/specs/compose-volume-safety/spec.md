# Spec Delta

## MODIFIED Requirements

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

## ADDED Requirements

### Requirement: External volumes keep their name
A volume declared `external: true`, or with an explicit `name:`, SHALL be
mounted under that name, without the project prefix.
#### Scenario: Shared PostgreSQL data
- **WHEN** `db` mounts external volume `shared_data` on its data directory
- **THEN** `container volume list` shows `shared_data` only, and no `<project>_shared_data` exists
