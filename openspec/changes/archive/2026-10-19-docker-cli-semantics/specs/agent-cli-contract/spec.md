# Spec Delta

## ADDED Requirements

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
