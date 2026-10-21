# compose-normalization Specification

## Purpose
Defines how the Docker CLI shim turns Compose files into the model it
orchestrates, so that a Compose file is either understood the way Docker
Compose understands it or refused.

## Requirements

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

### Requirement: The model keeps what orchestration reads
Every top-level Compose key that the shim reads when it builds, creates or
removes resources SHALL survive normalization, un-escaped as the services are,
and every key it drops SHALL be on a documented list verified by a test.

#### Scenario: Build secrets and labels
- **WHEN** a project uses build secrets and a volume or network label containing a literal `$`
- **THEN** the build uses the secret definition and the volume or network is created with a single `$`, not `$$`

#### Scenario: Deliberately dropped keys
- **WHEN** the normalizer output contains `configs`, `x-*` extensions or secrets that no build references
- **THEN** they are absent from the model and the test that lists the dropped keys passes

### Requirement: Every --env-file is preserved
`docker compose` SHALL accept `--env-file` any number of times and SHALL forward
every file to the official normalizer in the order given, so later files override
earlier ones exactly as Docker Compose resolves them.

#### Scenario: Two env files
- **WHEN** `e1.env` sets `B=1, C=1` and `e2.env` sets `C=2, D=2` and the user runs `docker compose --env-file e1.env --env-file e2.env config`
- **THEN** the model has `B=1, C=2, D=2`, identical to the official `docker-compose` output

#### Scenario: One env file
- **WHEN** a single `--env-file` is given
- **THEN** the behaviour is unchanged and `.env` is not read, as in Docker Compose

#### Scenario: Process environment wins
- **WHEN** a variable is set in the shell and in an env file
- **THEN** the shell value is the one interpolated

### Requirement: Option values accept both spellings
The long global options `--env-file`, `--project-directory`, `--file`,
`--profile` and `--project-name` SHALL accept a value either as the next
argument or after `=`, and a missing or empty value SHALL exit 64 naming the
option.

#### Scenario: Equals form
- **WHEN** the user runs `docker compose --env-file=e1.env --project-directory=app config`
- **THEN** the command behaves as with the separate-argument form instead of reporting an unsupported subcommand

#### Scenario: Empty value
- **WHEN** the user passes `--env-file ""`
- **THEN** the command exits 64 and does not silently ignore the option

### Requirement: Global and service environment files stay distinct
The global `--env-file` SHALL control interpolation only, and a service's
`env_file` SHALL control that service's environment only; neither SHALL be
read as the other.

#### Scenario: Both present
- **WHEN** `--env-file e1.env` sets `A` and a service has `env_file: svc.env` that sets `SVC`
- **THEN** `A` is available for interpolation, `SVC` is in the service environment, and `SVC` is not available for interpolation

### Requirement: Missing environment files fail early
A missing explicit env file SHALL end the command with a message naming the
file and exit 1 before any side effect, with or without the official normalizer.

#### Scenario: Missing file
- **WHEN** the user passes `--env-file nope.env` and the file does not exist
- **THEN** the command exits 1 with `couldn't find env file` and the absolute path, and the runtime records no call that creates anything

### Requirement: Fallback parser refuses environment inputs it cannot honour
Without the official normalizer, the built-in parser SHALL exit 64 for several
env files and for `COMPOSE_ENV_FILES`, telling the user to install
`docker-compose`, and SHALL never read an env file differently from the official
parser.

#### Scenario: Several env files without the official binary
- **WHEN** `docker-compose` is unavailable and two `--env-file` flags are given
- **THEN** the command exits 64 and says merging requires the official binary

#### Scenario: COMPOSE_ENV_FILES without the official binary
- **WHEN** `docker-compose` is unavailable and `COMPOSE_ENV_FILES` is set
- **THEN** the command exits 64 for the same reason

#### Scenario: COMPOSE_ENV_FILES with the official binary
- **WHEN** `docker-compose` is available and `COMPOSE_ENV_FILES=e1.env,e2.env` is set without `--env-file`
- **THEN** both files are used, and a `--env-file` on the command line takes precedence over the variable
