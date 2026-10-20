# oci-image-workflows Specification

## Purpose
Defines how images are built, run and published with the shim so that they can
be deployed on Linux targets such as AWS ECS and Fargate, and states clearly
what has and has not been verified for each architecture.

## Requirements

### Requirement: Standard builds
`docker build` SHALL build OCI images from a standard Dockerfile, supporting
`-f`, `-t`, `--build-arg`, `--target`, `--label`, `--platform`, `--no-cache`,
`--pull` and `--secret` wherever the runtime has an equivalent, and SHALL refuse
options without an equivalent.

#### Scenario: Build arguments and labels
- **WHEN** the user builds with `--build-arg VERSION=1.2 --label team=a`
- **THEN** the image config contains the label, and a `RUN` step sees `VERSION`

#### Scenario: Unsupported build option
- **WHEN** the user passes `--cache-from` or `--push`
- **THEN** the command exits 64 and names the option

### Requirement: Dockerfiles stay portable
The shim SHALL NOT require, add or rewrite anything in Dockerfiles, Compose
files or scripts to make them work on Apple `container`.

#### Scenario: Files are not modified
- **WHEN** a build or `compose up` runs
- **THEN** the project's Dockerfile and Compose files are byte-identical afterwards

### Requirement: Architecture claims are verified, not inferred
The documentation and `docker image inspect` SHALL report the architecture
recorded in the image, and the shim SHALL NOT state that an image supports
AMD64 because it was built on ARM64.

#### Scenario: ARM64 build
- **WHEN** an image is built on this machine without `--platform`
- **THEN** `docker image inspect --format '{{.Architecture}}'` reports `arm64`

#### Scenario: AMD64 build
- **WHEN** the user builds with `--platform linux/amd64` and the runtime supports it
- **THEN** the image reports `amd64`, and a run of that image reports `x86_64` from `uname -m`

#### Scenario: AMD64 not supported
- **WHEN** the runtime cannot build or run `linux/amd64`
- **THEN** the shim exits non-zero with the runtime's error and the documentation lists the limitation

### Requirement: Distinguish build, run and publish
The documentation SHALL separate four capabilities and state the verified status
of each on the supported release: building a single-platform image, running a
single-platform image, building a multi-platform image, and publishing a
manifest list or OCI index.

#### Scenario: Multi-platform request
- **WHEN** the user passes `--platform linux/amd64,linux/arm64` to `docker build`
- **THEN** the command either produces an image index that `docker image inspect` shows with both platforms, or exits 64 explaining that multi-platform output is unsupported

#### Scenario: Publish
- **WHEN** the user pushes a tag
- **THEN** the documentation states whether the registry receives a single-platform manifest or an index

### Requirement: Registry authentication and push
`docker login`, `docker tag` and `docker push` SHALL work against OCI registries,
including AWS ECR with a password supplied on standard input.

#### Scenario: ECR login
- **WHEN** the user runs `aws ecr get-login-password | docker login --username AWS --password-stdin <registry>`
- **THEN** the credential is stored by Apple `container` and the shim writes no file

#### Scenario: Push to a local registry
- **WHEN** an OCI registry runs in a disposable container and the user pushes a tagged image to it
- **THEN** pulling it back yields the same digest

### Requirement: Fargate and ECS limits are documented
The documentation SHALL state which properties of an image matter for ECS and
Fargate (platform of the task definition, index versus single manifest, ECR
authentication) and which of them the shim verifies.

#### Scenario: Documentation content
- **WHEN** a reader opens the AWS section
- **THEN** it states that a successful local ARM64 run does not prove an AMD64 task will work, and lists the manual steps for checking each architecture
