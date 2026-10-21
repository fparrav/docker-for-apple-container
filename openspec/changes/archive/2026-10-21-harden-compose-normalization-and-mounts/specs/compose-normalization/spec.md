# Spec Delta

## ADDED Requirements

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
