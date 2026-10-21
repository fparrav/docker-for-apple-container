# compose-build-secrets Specification

## Purpose
Defines how Compose `build.secrets` reach an Apple `container` build: which
definitions are honoured, how each secret is identified and delivered, what is
checked before any side effect, and how secret values stay out of every output.

## Requirements

### Requirement: Build secrets survive normalization
A project whose services declare `build.secrets` SHALL build when the official
normalizer is used, exactly as it does with the built-in parser, because the
definitions those secrets name are kept in the model the shim orchestrates.

#### Scenario: File secret through the official normalizer
- **WHEN** a service has `build.secrets: [npm_token]` and the top-level `npm_token` has `file: ./npm-token.txt`
- **THEN** `docker compose build` passes `--secret id=npm_token,src=<absolute path of npm-token.txt>` to the Apple build and does not exit 64

#### Scenario: Unreferenced definitions are not carried
- **WHEN** a top-level secret is used only by `services.<name>.secrets` or by nothing
- **THEN** the orchestration model holds no definition for it and no build argument is produced for it

### Requirement: Secret id follows Compose
The identifier the Dockerfile sees SHALL be the entry's `target` when present
and its `source` otherwise, as Docker Compose passes it to BuildKit.

#### Scenario: Target renames the id
- **WHEN** a service lists `{source: npm_token, target: registry}` and the Dockerfile reads `RUN --mount=type=secret,id=registry`
- **THEN** the build receives `id=registry` and the `RUN` step can read the secret

#### Scenario: Default id
- **WHEN** a service lists `npm_token` without `target`
- **THEN** the build receives `id=npm_token`

### Requirement: File secrets are delivered by path
A file-based secret SHALL reach the build as a path resolved against the
Compose project directory, and the shim SHALL NOT read, copy or print the file.

#### Scenario: Relative path with a project directory
- **WHEN** `file: ./npm-token.txt` is declared and the command runs with `--project-directory app` from another directory
- **THEN** the build argument names `<cwd>/app/npm-token.txt` as an absolute path

#### Scenario: Path containing a dollar sign
- **WHEN** the secret file path contains a literal `$`
- **THEN** the build argument contains a single `$`, not the `$$` escape the normalizer adds

### Requirement: Environment secrets come from the process environment
An environment-based secret SHALL be delivered by variable name, and its
variable MUST be set in the environment of the shim process; the value SHALL
NOT be placed in any argument.

#### Scenario: Variable exported
- **WHEN** `environment: NPM_TOKEN` is declared and `NPM_TOKEN` is exported
- **THEN** the build argument is `--secret id=<id>,env=NPM_TOKEN` and the value appears in no argument, log or message

#### Scenario: Variable only in a dotenv file
- **WHEN** `NPM_TOKEN` is defined in `.env` or `--env-file` but not exported
- **THEN** the command exits 64 before any side effect, names the variable, and says it must be exported for the shim

### Requirement: Secrets are checked before side effects
`up` and `build` SHALL verify every build secret of the services they act on
before creating any network, volume, container or image, and SHALL report all
problems together as `REFUSED:` lines with exit 64.

#### Scenario: Missing secret file
- **WHEN** a service that will be built names a secret whose file does not exist
- **THEN** the command exits 64, the message names the service, the secret id and the path, and the runtime records no `network create`, `volume create`, `build` or `run`

#### Scenario: Secret not defined, external, or ambiguous
- **WHEN** a build names a secret that has no definition, is `external: true`, or declares both `file` and `environment` or neither
- **THEN** the command exits 64 naming the secret and the reason

#### Scenario: No build happens
- **WHEN** `up` finds the image already present and no `--build` is given
- **THEN** a missing secret file does not stop `up`, as in Docker Compose, while a structurally invalid definition still does

### Requirement: Secret values never appear in output
No secret value SHALL appear in standard output, standard error, error
messages, container labels, the configuration hash or `docker compose config`
output produced by the shim.

#### Scenario: Failing build
- **WHEN** a build with an environment secret fails
- **THEN** neither the shim's messages nor its captured output contain the variable's value

#### Scenario: Config output
- **WHEN** the user runs `docker compose config`
- **THEN** secrets appear as the official renderer prints them (path or variable name), never as values

### Requirement: Runtime secrets stay refused
`services.<name>.secrets` SHALL remain refused with exit 64, not waivable, and a
secret used by both a build and a service SHALL NOT make the service accepted.

#### Scenario: Same secret in build and runtime
- **WHEN** `npm_token` is listed in `build.secrets` and in `services.api.secrets`
- **THEN** the build argument is produced but `up` exits 64 with the existing runtime-secrets refusal

### Requirement: Build secret attributes follow Compose
The attributes `uid`, `gid` and `mode` on a build secret entry SHALL be accepted
without effect, because Docker Compose does not pass them to the build, and any
other attribute SHALL be refused.

#### Scenario: Ignored ownership attributes
- **WHEN** an entry sets `uid: "1000"` and `mode: 0400`
- **THEN** the build argument is unchanged and the shim prints a `NOT APPLIED:` notice naming the attributes

#### Scenario: Unknown attribute
- **WHEN** an entry carries an attribute that Compose's schema accepts but the shim does not classify
- **THEN** the command exits 64 naming the attribute
