## ADDED Requirements

### Requirement: The built wheel is installed and exercised before release
Before a release is published, the wheel built from the tagged commit SHALL be
installed into a clean virtual environment, and the installed `docker` executable
SHALL report the shim version of the tag, run with a stand-in for the Apple
`container` CLI, and exit 1 with a message naming the missing CLI when none is
available. The wheel that passes SHALL be the one attached to the release.

#### Scenario: Clean install
- **WHEN** the checks job builds the wheel and installs it into a new virtual environment
- **THEN** `docker version` runs from that environment and its `Shim:` line equals the version derived from the tag

#### Scenario: Nothing from the source tree
- **WHEN** the installed executable starts
- **THEN** the package is imported from the environment's `site-packages`, not from the checkout

#### Scenario: Runtime absent
- **WHEN** the installed executable runs without an Apple `container` CLI on the path
- **THEN** it exits 1 and the message says the CLI was not found and how to point to it

#### Scenario: Entry point missing
- **WHEN** the built wheel lacks the `docker` entry point or the package files
- **THEN** the checks fail and no release is created

#### Scenario: Declared Python floor
- **WHEN** the check runs on the lowest Python version that `requires-python` allows
- **THEN** the installed executable runs, or the floor in `pyproject.toml` is raised to the lowest version that passes

#### Scenario: Same wheel published
- **WHEN** the release is created
- **THEN** the files attached to it are the ones the checks installed
