# Spec Delta

## Purpose

Defines how the Docker CLI shim turns Compose files into the model it
orchestrates, so that a Compose file is either understood the way Docker
Compose understands it or refused.

## ADDED Requirements

### Requirement: Official normalizer when available
When the official Docker Compose executable is available, the shim SHALL obtain
the Compose model from its JSON rendering of the merged configuration and SHALL
NOT interpret the YAML itself.

#### Scenario: Normalizer present
- **WHEN** `docker-compose` is on `PATH` and the user runs `docker compose up -d`
- **THEN** the project model comes from `docker-compose config --format json` and the active mode is reported as `official` by `docker compose version`

#### Scenario: Explicit executable
- **WHEN** `CONTAINER_DOCKER_SHIM_COMPOSE` names an executable
- **THEN** the shim runs that executable and does not search `PATH`

#### Scenario: No Docker daemon needed
- **WHEN** no Docker Engine is running and the user runs `docker compose config`
- **THEN** the command succeeds and prints the normalized configuration

### Requirement: No self-invocation
The shim SHALL NOT invoke `docker compose` to normalize, and SHALL refuse an
executable that resolves to the shim itself.

#### Scenario: Resolved binary is the shim
- **WHEN** `CONTAINER_DOCKER_SHIM_COMPOSE` points to the shim's own launcher
- **THEN** the command exits non-zero with a message naming the loop, and no subprocess recursion occurs

### Requirement: Compose inputs are honoured
The shim SHALL support several `-f` files merged in order, `.env` and
`--env-file`, variable interpolation, YAML anchors, aliases and multiline
strings, `profiles`, `env_file`, `build.context`, `build.dockerfile`,
`build.args`, relative paths and the project name resolution order.

#### Scenario: Multiple files
- **WHEN** the user runs `docker compose -f compose.yaml -f compose.override.yaml config`
- **THEN** the later file overrides the earlier one as Docker Compose does

#### Scenario: Profiles
- **WHEN** a service has `profiles: [debug]` and the user runs `docker compose up -d` without `--profile debug`
- **THEN** that service is not created, and with `--profile debug` it is created

#### Scenario: Anchors and multiline scalars
- **WHEN** a Compose file uses YAML anchors, aliases and a `|` block scalar for `command`
- **THEN** the resulting service has the same values Docker Compose renders

#### Scenario: Relative build context
- **WHEN** a service sets `build: ./api` and the command runs from another directory with `--project-directory`
- **THEN** the build context is the absolute path resolved against the project directory

### Requirement: Fallback parser refuses what it cannot read
When the official executable is not available, the shim SHALL use its built-in
parser only for the documented subset and SHALL exit 64 for constructs outside it.

#### Scenario: Anchor without the official binary
- **WHEN** `docker-compose` is unavailable and the file uses a YAML anchor or alias
- **THEN** the command exits 64, names the unsupported construct, and tells the user to install `docker-compose`

#### Scenario: Multiple files without the official binary
- **WHEN** `docker-compose` is unavailable and the user passes two `-f` files
- **THEN** the command exits 64 and explains that merging requires the official binary

#### Scenario: Subset still works
- **WHEN** `docker-compose` is unavailable and the file uses only block maps, sequences, quoted scalars and `${VAR:-default}`
- **THEN** the command behaves as before

### Requirement: Stable parser-runtime interface
The orchestration code SHALL depend only on the normalized project dictionary,
so that either parser source produces the same orchestration behaviour for the
same model.

#### Scenario: Equivalent models
- **WHEN** the same simple project is loaded through the official normalizer and through the built-in parser
- **THEN** the sequence of runtime commands produced by `up` is identical

### Requirement: Unknown configuration is not interpreted silently
The shim SHALL report every service key that is neither translated nor
classified as informational, and SHALL refuse keys classified as restrictions.

#### Scenario: Unknown key
- **WHEN** a service contains a key that the shim does not know
- **THEN** the shim prints a warning naming the service and the key, and continues only if the key is not a restriction

### Requirement: Installation guidance
The documentation SHALL describe how to install the official executable without
Docker Desktop and how to tell which normalizer is active.

#### Scenario: Install instructions
- **WHEN** the user reads the Compose section of the README
- **THEN** it gives the install command for `docker-compose`, states that no Docker Engine is required for `config`, and describes the fallback limits
