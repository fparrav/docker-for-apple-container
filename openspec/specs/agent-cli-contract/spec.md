# agent-cli-contract Specification

## Purpose
Defines what automated callers such as AI agents and build tools can rely on
from the `docker` executable.

## Requirements

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

### Requirement: Declared formats exist
Every command and output format that the README or the compatibility matrix
declares supported SHALL be accepted by the command and covered by a contract
test, and every format that is not supported SHALL be listed as UNSUPPORTED in
the matrix.
#### Scenario: Claim without an implementation
- **WHEN** the documentation names an option or format that the command rejects
- **THEN** the documentation test fails naming the claim
#### Scenario: Unsupported format listed
- **WHEN** a reader looks for `docker compose ps --format '{{.Name}}'`
- **THEN** the matrix lists it as UNSUPPORTED with the reason
#### Scenario: Runtime failure keeps streams clean
- **WHEN** the runtime fails while a formatted command runs
- **THEN** standard output contains no partial listing and standard error holds the message

### Requirement: Generic consumer contract
The suite SHALL include a contract test for a generic non-interactive consumer
that lists the commands and output shapes any script can rely on: `docker ps
-a --format`, `docker inspect --format '{{json .}}'`, `docker compose ps
--format json`, `docker compose config`, exit codes of `run`/`exec`, and the
exit-64 refusal of unsupported options.
#### Scenario: Shape change detected
- **WHEN** a change alters a field name or type in `docker compose ps --format json`
- **THEN** the generic contract test and, where relevant, the Hermes and OpenClaw contract tests fail

### Requirement: Version probes
`docker version` SHALL accept `--format` / `-f` with `json` and the bounded
templates `{{.Client.Version}}` and `{{.Server.Version}}`, so that scripts that
probe the Docker version get a non-empty value and exit status 0, and SHALL
refuse every other template with exit 64.
#### Scenario: Server version probe
- **WHEN** an agent runs `docker version --format '{{.Server.Version}}'`
- **THEN** standard output is one non-empty line with the Apple `container` version and the exit status is 0
#### Scenario: JSON
- **WHEN** an agent runs `docker version --format json`
- **THEN** standard output is valid JSON with `Client.Version` and `Server.Version` strings
#### Scenario: Unsupported template
- **WHEN** the template names another field
- **THEN** the command exits 64 naming the field
