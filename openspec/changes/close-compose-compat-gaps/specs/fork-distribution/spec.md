# Spec Delta

## ADDED Requirements

### Requirement: Documented install commands are valid as written
Every install command in `README.md` and in the release documentation SHALL be a
command the tool accepts, with the same arguments, for the release tag or the
source checkout it documents, and a test SHALL reject the argument orders known
to be invalid.

#### Scenario: uv from a release tag
- **WHEN** a user runs the documented `uv tool install` command with a real release tag
- **THEN** uv installs `docker-for-apple-container-fork` and a `docker` executable appears in the tool bin directory

#### Scenario: Inverted --from
- **WHEN** a documentation file contains `uv tool install --from docker-for-apple-container-fork <url>`
- **THEN** the documentation test fails

#### Scenario: One command everywhere
- **WHEN** `README.md` and `docs/releasing.md` both document the `uv` release install
- **THEN** they use the same command
