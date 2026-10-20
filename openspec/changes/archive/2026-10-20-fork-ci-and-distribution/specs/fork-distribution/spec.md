# Spec Delta

## Purpose

Defines how the fork is named, installed, versioned, released and identified,
so that a user can install exactly this project, tell which version and
runtime it uses, and never depend on the upstream project's infrastructure.

## ADDED Requirements

### Requirement: Independent identity
The package metadata, README, badges, links and release workflow SHALL identify
the fork's own repository and distribution name, SHALL NOT use a distribution
name that collides with the upstream project's, and SHALL credit upstream and
keep its licence notice.
#### Scenario: Metadata
- **WHEN** a reader opens the package metadata
- **THEN** the project URLs point to the fork's repository and the distribution name differs from the upstream package name
#### Scenario: Attribution
- **WHEN** a reader opens the README
- **THEN** it states that the project is a fork and links to upstream, and the licence file is unchanged
#### Scenario: No upstream infrastructure
- **WHEN** the repository files are searched for the upstream package index page, tap or website
- **THEN** none is used as an install source or release target

### Requirement: Installation from the fork
The README SHALL document installation from a release tag of the fork with `uv`
and with `pipx`, installation from a source checkout, and verification that the
installed `docker` is this project, for interactive and non-interactive shells.
#### Scenario: Tool install
- **WHEN** a user follows the `uv` instructions
- **THEN** a `docker` executable exists on `PATH` and `docker version` shows the shim version and the Apple `container` version
#### Scenario: Competing docker
- **WHEN** another `docker` appears earlier on `PATH`
- **THEN** the documentation shows the command that reveals the order and how to put this one first
#### Scenario: Non-interactive agent
- **WHEN** an agent starts with a minimal environment
- **THEN** the documentation gives the absolute path or `PATH` setting that makes `docker version` succeed there

### Requirement: Identifiable version and runtime
`docker version` SHALL print the shim's own version and the Apple `container`
version it controls, and the shim version SHALL come from the release tag.
#### Scenario: Installed release
- **WHEN** the package was built from tag `v0.3.0`
- **THEN** `docker version` shows shim version `0.3.0` and the supported runtime version
#### Scenario: Source checkout without a tag
- **WHEN** the shim runs from an untagged checkout
- **THEN** it shows a development version that cannot be mistaken for a release

### Requirement: Semantic versioning and release notes
Releases SHALL use semantic versions created from Git tags, and each release
note SHALL state the supported Apple `container` version, the breaking changes,
and which test tiers were run and which were not run.
#### Scenario: Release note content
- **WHEN** a release is published
- **THEN** its notes include the supported runtime version and a tier table with passed, failed, skipped and not run counts
#### Scenario: Runtime evidence missing
- **WHEN** the live tier was not run for the release candidate
- **THEN** the notes say "live tier: not run"

### Requirement: Release channel
Releases SHALL be published as GitHub Releases containing the built source and
wheel distributions, and publishing to a package index SHALL be disabled unless
the owner explicitly enables it.
#### Scenario: Tag pushed
- **WHEN** a version tag is pushed
- **THEN** the workflow builds the distributions, runs the hosted-runner checks and attaches the files to a GitHub Release, and publishes nothing to a package index
#### Scenario: Index publishing off
- **WHEN** the owner has not enabled index publishing
- **THEN** the workflow contains no step that can publish to one
