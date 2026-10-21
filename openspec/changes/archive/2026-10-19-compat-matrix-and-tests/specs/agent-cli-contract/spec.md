# Spec Delta

## Purpose

Defines what automated callers such as AI agents and build tools can rely on
from the `docker` executable.

## ADDED Requirements

### Requirement: Works without a shell profile
The `docker` executable SHALL work when launched as a subprocess with a minimal
environment, resolved only through `PATH`, without aliases or shell
configuration files.

#### Scenario: Minimal environment
- **WHEN** a subprocess runs `docker version` with an environment containing only `PATH` and `HOME`
- **THEN** it exits 0 and prints version output

### Requirement: Stream and exit-code fidelity
The shim SHALL propagate standard input, standard output and standard error of
the executed command and SHALL return the container process's exit code for
`run`, `exec` and `compose exec`.

#### Scenario: Exit code
- **WHEN** a caller runs `docker exec c sh -c 'exit 7'`
- **THEN** the command exits 7

#### Scenario: Standard input
- **WHEN** a caller pipes text into `docker exec -i c cat`
- **THEN** the same text appears on standard output

#### Scenario: Separate streams
- **WHEN** a command writes to standard error inside the container
- **THEN** it appears on the shim's standard error and not on standard output

### Requirement: Signals
Termination signals sent to the shim during an attached command SHALL be
delivered to the underlying command and SHALL NOT leave orphan processes.

#### Scenario: Interrupt
- **WHEN** a caller sends SIGTERM to a running `docker run` without `-d`
- **THEN** the underlying `container` process ends and no shim process remains

### Requirement: Stable machine-readable output
`docker version`, `docker info`, `docker ps --format`, `docker inspect
--format`, `docker images --format`, `docker logs` and `docker compose ps
--format json` SHALL produce the documented shapes so that agents can parse
them.

#### Scenario: Ps format
- **WHEN** an agent runs `docker ps --format '{{.ID}} {{.Names}} {{.Status}}'`
- **THEN** each line has the three fields

#### Scenario: Inspect labels
- **WHEN** an agent runs `docker inspect --format '{{json .Config.Labels}}' c`
- **THEN** the output is a JSON object containing the labels given to `docker run --label`

#### Scenario: Environment variables
- **WHEN** an agent starts a container with `-e KEY=value` and inspects `.Config.Env`
- **THEN** the list contains `KEY=value`

### Requirement: Clear failure for missing capabilities
When a command or option is unsupported, the shim SHALL exit with a non-zero
code, write a message to standard error that names the command or option and the
reason, and SHALL NOT print anything resembling a success on standard output.

#### Scenario: Unsupported command
- **WHEN** an agent runs `docker commit c img`
- **THEN** the exit code is 64 and standard error names `commit`

### Requirement: Engine API users are identified as incompatible
The documentation SHALL list tool categories that need the Docker Engine API or
`/var/run/docker.sock` as incompatible, and the shim SHALL NOT present a
fabricated Engine.

#### Scenario: Socket-based tool
- **WHEN** a reader looks for tools that talk to the Docker socket
- **THEN** the README lists them as unsupported and states that the shim exposes no socket and no Engine API

#### Scenario: No faked capabilities
- **WHEN** an agent runs `docker info`
- **THEN** the output does not claim Engine features, such as Swarm, that the runtime does not provide

### Requirement: Contract tests for named consumers
The suite SHALL include contract tests for each named consumer (Hermes and
OpenClaw today) listing the exact commands and output shapes they rely on.

#### Scenario: Consumer contract
- **WHEN** the suite runs
- **THEN** a failing change in an output shape that a named consumer parses is detected by that consumer's contract test
