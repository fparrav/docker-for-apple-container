# run-security-options Specification

## Purpose
Defines how the Docker CLI shim treats security and resource restrictions
requested through `docker run`, `docker create` and Compose services, so that a
restriction is either applied to the container or the command fails before the
container is created.

## Requirements

### Requirement: Option classification
Every security or resource option of `run`, `create` and Compose services SHALL
be classified as SUPPORTED, PARTIAL or UNSUPPORTED, and the classification SHALL
be available to users in the documentation and to the code that translates
commands.

#### Scenario: Classification is documented
- **WHEN** the user reads the compatibility documentation
- **THEN** each of `--security-opt`, `--cap-add`, `--cap-drop`, `--pids-limit`, `--privileged`, `--read-only`, `--tmpfs`, `--user`, `--network`, `--memory`, `--cpus`, `--ulimit` and the matching Compose keys appears with exactly one classification and a reason

#### Scenario: Documentation matches behaviour
- **WHEN** the test suite runs
- **THEN** a test fails if an option's documented classification differs from how the translator treats it

### Requirement: Unsupported restrictions fail before creation
When a command requests an UNSUPPORTED option, the shim SHALL exit non-zero
with exit code 64, name the option and the reason, and SHALL NOT start or create
any container.

#### Scenario: Security option without equivalent
- **WHEN** the user runs `docker run --security-opt no-new-privileges alpine true`
- **THEN** the command exits 64, the message names `--security-opt`, and `container ls -a` shows no new container

#### Scenario: PID limit
- **WHEN** the user runs `docker run --pids-limit 100 alpine true`
- **THEN** the command exits 64 and no container is created

#### Scenario: Privileged mode
- **WHEN** the user runs `docker run --privileged alpine true`
- **THEN** the command exits 64 and no container is created

#### Scenario: Compose security key
- **WHEN** a Compose service declares `privileged: true`, `security_opt`, `sysctls` or `devices` and the user runs `docker compose up -d`
- **THEN** the command exits 64 before any container of the project is created, and the message names the service and the key

### Requirement: Explicit opt-in for unenforced options
The shim SHALL allow an UNSUPPORTED option to be skipped only when the user names
that option in an explicit environment setting, and SHALL print a warning naming
the option every time it is skipped.

#### Scenario: Default is refuse
- **WHEN** no opt-in setting is present and a command uses an UNSUPPORTED option
- **THEN** the command is refused as in the fail-before-creation requirement

#### Scenario: Opt-in is per option
- **WHEN** the opt-in names `security-opt` and the command also uses `--pids-limit`
- **THEN** `--security-opt` is skipped with a warning and the command is still refused because of `--pids-limit`

### Requirement: Supported options take effect
Options classified as SUPPORTED SHALL be forwarded so that the runtime applies
them, and the applied value SHALL be observable through `docker inspect`.

#### Scenario: Capability drop
- **WHEN** the user runs a container with `--cap-drop ALL --cap-add NET_BIND_SERVICE`
- **THEN** `docker inspect` reports the corresponding `CapAdd` and `CapDrop`, and a process in the container that needs a dropped capability fails

#### Scenario: Read-only root filesystem
- **WHEN** the user runs a container with `--read-only`
- **THEN** writing to the root filesystem inside the container fails and `docker inspect` reports `ReadonlyRootfs` true

#### Scenario: Non-root user
- **WHEN** the user runs a container with `--user 1000:1000`
- **THEN** `id -u` inside the container prints 1000

#### Scenario: Resource limits
- **WHEN** the user runs a container with `--memory 256m --cpus 1`
- **THEN** `docker inspect` reports those limits

### Requirement: Tmpfs options are applied
The shim SHALL forward the complete `--tmpfs` value, including its mount options,
to the runtime, and SHALL NOT discard any option.

#### Scenario: Tmpfs path only
- **WHEN** the user runs a container with `--tmpfs /tmp`
- **THEN** a tmpfs is mounted at `/tmp`

#### Scenario: Restrictive tmpfs options
- **WHEN** the user runs a container with `--tmpfs /tmp:rw,noexec,nosuid,size=64m`
- **THEN** the mount inside the container shows `noexec`, `nosuid` and a 64 MiB size

#### Scenario: Invalid tmpfs option
- **WHEN** the runtime rejects a tmpfs option
- **THEN** the shim exits non-zero with the runtime's message and no container is left behind

### Requirement: Partial options are never reduced silently
If an option is classified PARTIAL, the shim SHALL refuse with exit code 64 any
requested part the runtime cannot apply, instead of discarding it.

#### Scenario: Partial option with an unsupported part
- **WHEN** a PARTIAL option is used with a part that has no runtime equivalent
- **THEN** the command exits 64 naming the part and no container is created

### Requirement: Capability names are portable
The shim SHALL accept capability names in Docker's form (`NET_ADMIN`, `ALL`) and
SHALL ensure the runtime applies them, or refuse the name with a clear error.

#### Scenario: Docker-style capability
- **WHEN** the user runs a container with `--cap-add NET_ADMIN`
- **THEN** the runtime receives a valid capability and `docker inspect` lists it

#### Scenario: Unknown capability
- **WHEN** the user passes `--cap-add NOT_A_CAP`
- **THEN** the command exits non-zero with the runtime's message and no container is created

### Requirement: Isolation requests cannot be waived
A request for network isolation (`docker run --network none`, Compose
`network_mode`) SHALL be refused even when the unenforced-options variable
names it, because skipping it gives the container more connectivity than was
requested.
#### Scenario: Run with no network
- **WHEN** the variable lists `network-none` and the user runs `docker run --network none alpine true`
- **THEN** the command exits 64 and states that isolation requests cannot be waived
#### Scenario: Other waivers keep working
- **WHEN** the variable lists `pids-limit` and the user runs `docker run --pids-limit 100 alpine true`
- **THEN** the option is skipped, a `NOT APPLIED:` line is printed and the command runs

### Requirement: Unenforced options are reported unmistakably
Every option that is skipped or applied only in part, in `docker run`,
`docker create` and Compose, SHALL produce one `NOT APPLIED:` line on standard
error naming the option and the reason, SHALL leave standard output unchanged,
and SHALL keep the exit status of the command.
#### Scenario: Container id stays parseable
- **WHEN** an allowed option is skipped by `docker run -d`
- **THEN** standard output contains only the container id and standard error contains the `NOT APPLIED:` line
#### Scenario: Refusal wording
- **WHEN** an option is refused
- **THEN** standard error starts with `REFUSED:`, the exit code is 64 and the message names the variable that would waive it, unless the option cannot be waived

### Requirement: Waiver names are validated
Names in the unenforced-options variable that match no waivable option SHALL
be reported with a warning on every command, so that a typo cannot create a
false sense of coverage, and only the options documented as waivable SHALL be
accepted.
#### Scenario: Typo
- **WHEN** the variable contains `pid-limit` and the user runs any command
- **THEN** a warning names `pid-limit` as unknown and lists the valid names
#### Scenario: Non-waivable name
- **WHEN** the variable contains `network-none`
- **THEN** the warning says the option cannot be waived
