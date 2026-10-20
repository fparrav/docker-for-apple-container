# Spec Delta

## MODIFIED Requirements

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

## ADDED Requirements

### Requirement: Shim version is reported
`docker version` SHALL print the installed shim version in addition to the
Apple `container` version, without removing the existing lines.

#### Scenario: Version output
- **WHEN** the user runs `docker version`
- **THEN** the output contains a `Shim:` line with the version, the existing `Client:` and `Server:` lines are unchanged, and the exit status is 0
