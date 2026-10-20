# runtime-compatibility Specification

## Purpose
Defines which Apple `container` release the Docker CLI shim supports and how
that support is declared, enforced and verified, so that behavior never depends
on guessing alternate runtime schemas.

## Requirements

### Requirement: Single supported runtime release
The shim SHALL support exactly one Apple `container` release, 1.5.0, and SHALL
NOT include backward-compatibility paths for earlier releases.

#### Scenario: Supported release reported by the shim
- **WHEN** the user asks the shim for its help or supported-runtime text
- **THEN** it states that Apple `container` 1.5.0 is the supported runtime

#### Scenario: Version command against the supported runtime
- **WHEN** the user runs `docker version` with Apple `container` 1.5.0 running
- **THEN** the output reports container CLI version 1.5.0

### Requirement: Strict runtime JSON parsing
The shim SHALL read runtime JSON using the supported release's schema directly
and SHALL reject payloads that lack required fields instead of probing
alternate or historical field names.

#### Scenario: Unexpected list payload
- **WHEN** the runtime returns JSON that is not the expected array for a list or inspect command
- **THEN** the shim exits non-zero with an error naming Apple `container` 1.5.0 and the expected shape

#### Scenario: Missing required field
- **WHEN** a runtime payload omits a field the translation requires
- **THEN** the shim reports an error naming Apple `container` 1.5.0 and does not guess a substitute field

### Requirement: Translations verified on the supported runtime
Before a release is declared supported, every translated command family SHALL
produce Docker-shaped output from the live runtime without parse errors.

#### Scenario: Read-only command matrix
- **WHEN** version, info, ps, images, inspect, image inspect, port, logs and compose ps run against the live 1.5.0 runtime
- **THEN** each command exits successfully and its output parses without error

#### Scenario: Container lifecycle
- **WHEN** a disposable container is created with run -d, inspected, exercised with exec, stopped and removed through the shim on 1.5.0
- **THEN** each step succeeds, the inspected state matches the runtime, and the container no longer exists afterwards

### Requirement: Runtime executable resolution
The shim SHALL locate the Apple `container` executable from the
`CONTAINER_DOCKER_SHIM_CONTAINER` variable when set, otherwise from `PATH`, and
SHALL NOT depend on a single fixed install location.

#### Scenario: Explicit override
- **WHEN** `CONTAINER_DOCKER_SHIM_CONTAINER` names an executable
- **THEN** the shim runs that executable and does not search `PATH`

#### Scenario: Homebrew install without configuration
- **WHEN** `container` is only installed at `/opt/homebrew/bin` and is on `PATH`, with no override set
- **THEN** the shim finds it and runs commands successfully

#### Scenario: Executable not found
- **WHEN** the override is unset and no `container` executable is on `PATH`
- **THEN** the shim exits non-zero with an error that names both the override variable and `PATH`

### Requirement: Image metadata reflects the image
`docker images` SHALL report each image's size from the image itself, not from
the OCI index descriptor, and SHALL report a creation time only when the runtime
provides a valid one.

#### Scenario: Multi-platform image size
- **WHEN** an image is stored as an OCI index with an arm64 variant
- **THEN** its reported size is not the byte size of the index manifest

#### Scenario: Image without a creation time
- **WHEN** the runtime provides no valid creation time for an image
- **THEN** the shim does not print a 1970 timestamp or an age of decades

### Requirement: Runtime state stays authoritative
The shim SHALL NOT store runtime state of its own and SHALL derive container,
image and network state from the Apple `container` runtime on every call.

#### Scenario: Validation leaves no shim-owned state
- **WHEN** the 1.5.0 validation completes
- **THEN** no shim-owned state files exist and every resource the validation created has been removed

### Requirement: Documentation matches the supported release
The supported release stated in the README SHALL equal the release the shim
enforces, the repository agent guidance SHALL NOT duplicate that version as a
literal, and a test SHALL fail when any other document in the repository states
a runtime release that differs from the enforced one.

#### Scenario: README and code agree
- **WHEN** the README requirements and test-baseline text are compared with the shim's supported release
- **THEN** all of them name 1.5.0

#### Scenario: No stale release literal
- **WHEN** the repository is searched for the previous release string 1.4.1 outside of git history
- **THEN** no match remains in the code, tests or docs, and the agent guidance refers to the code for the supported version

#### Scenario: Other documents are scanned
- **WHEN** a document under `docs/` or the agent guidance names an Apple `container` release different from the enforced one
- **THEN** the documentation test fails naming the file and the literal

#### Scenario: Guidance absent
- **WHEN** a checkout has no agent guidance file
- **THEN** the test reports that document as not checked instead of passing silently

### Requirement: Shim version is reported
`docker version` SHALL print the installed shim version in addition to the
Apple `container` version, without removing the existing lines.

#### Scenario: Version output
- **WHEN** the user runs `docker version`
- **THEN** the output contains a `Shim:` line with the version, the existing `Client:` and `Server:` lines are unchanged, and the exit status is 0
