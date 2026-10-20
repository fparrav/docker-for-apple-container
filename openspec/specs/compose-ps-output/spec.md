# compose-ps-output Specification

## Purpose

Defines the output of `docker compose ps`, so that people and agents can list
the containers of a project in the same shapes Docker Compose prints.

## Requirements

### Requirement: Default table
`docker compose ps` without a format SHALL print a table whose header and column
order match Docker Compose (`NAME`, `IMAGE`, `COMMAND`, `SERVICE`, `CREATED`,
`STATUS`, `PORTS`), listing running containers, and `-a` SHALL include stopped
ones.
#### Scenario: Running project
- **WHEN** a project has two running services and the user runs `docker compose ps`
- **THEN** the output has the header and one row per container with the service name in the `SERVICE` column
#### Scenario: Stopped container
- **WHEN** a service has exited and the user runs `docker compose ps -a`
- **THEN** its row appears with an exited status; the numeric code Docker prints
  in parentheses is omitted, because Apple container 1.5.0 exposes no numeric
  exit code and the table must not invent one

### Requirement: JSON format
`docker compose ps --format json` SHALL print valid JSON with the document layout and field types that the Docker Compose release used for conformance prints. Each record SHALL have, at least, `ID`, `Name`, `Image`, `Command`, `Project`, `Service`, `State`, `Status`, `Health`, `ExitCode` and `Publishers`.
#### Scenario: Parseable output
- **WHEN** an agent runs `docker compose ps --format json -a` and parses every record
- **THEN** each record has the listed fields with string, integer and array types as Docker Compose prints them
#### Scenario: States
- **WHEN** a container is running or created but not started
- **THEN** `State` is `running` or `created` with `ExitCode` 0
#### Scenario: Exited container
- **WHEN** a container has exited and its numeric exit code is not exposed by the
  supported runtime
- **THEN** the command exits 64, prints the refusal on standard error, and prints
  nothing on standard output
#### Scenario: Empty project
- **WHEN** the project has no containers
- **THEN** the output is valid for the same layout (an empty document) and the exit status is 0

### Requirement: Filters and quiet modes
`docker compose ps` SHALL support `-q`, `--services`, `--status STATE` (repeatable)
and service name arguments, and SHALL refuse Go-template formats with exit 64.
#### Scenario: Quiet
- **WHEN** the user runs `docker compose ps -q`
- **THEN** only container ids are printed, one per line
#### Scenario: Services only
- **WHEN** the user runs `docker compose ps --services`
- **THEN** the names of the project's services are printed, one per line
#### Scenario: Status filter
- **WHEN** the user runs `docker compose ps -a --status exited`
- **THEN** only exited containers are listed
#### Scenario: Template
- **WHEN** the user passes `--format '{{.Name}}'`
- **THEN** the command exits 64 and the message says only `table` and `json` are supported

### Requirement: Streams and status
The listing SHALL go to standard output, errors to standard error, and the
exit status SHALL be 0 when the listing succeeded, including when it is empty.
#### Scenario: Runtime failure
- **WHEN** the runtime query fails
- **THEN** the command exits non-zero, prints the runtime's message on standard error and prints nothing resembling a listing on standard output

### Requirement: Exited exit codes are refused, not fabricated
Apple container 1.5.0's list and inspect JSON expose no numeric exit-code field
(verified against the supported release), so the shim cannot fill Compose's
integer `ExitCode` for an exited container. `docker compose ps --format json`
SHALL refuse a listing that includes such a container with exit 64 and no
standard output; the default table SHALL keep listing exited rows with the code
omitted, and `-q`, `--services`, and `--status` filtering SHALL keep working.
#### Scenario: JSON listing with an exited container
- **WHEN** the user runs `docker compose ps --format json -a` and any listed
  container has exited
- **THEN** the command exits 64, names the exited container and the missing exit
  code on standard error, and prints nothing on standard output
#### Scenario: Table listing with an exited container
- **WHEN** the user runs `docker compose ps -a` and a service has exited
- **THEN** the table lists it with an `Exited` status without a fabricated code
#### Scenario: Quiet and services listings
- **WHEN** the user runs `docker compose ps -q -a` or `docker compose ps --services`
- **THEN** the ids or service names are printed and the exit status is 0
