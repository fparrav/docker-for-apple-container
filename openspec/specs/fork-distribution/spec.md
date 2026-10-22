# fork-distribution Specification

## Purpose

Defines how the fork is named, installed, versioned, released and identified,
so that a user can install exactly this project, tell which version and
runtime it uses, and never depend on the upstream project's infrastructure.

## Requirements

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
- **WHEN** the hosted run did not execute the live tier
- **THEN** the tier table says "live tier: not run" and the live evidence section is the only claim about live results

### Requirement: Release channels and evidence in notes
A tag of the form `vX.Y.Z` SHALL be a final release. A PEP 440 pre-release tag
(`rc`, `a`, `b`, `dev`) SHALL be published as a GitHub pre-release. The notes of
a final release SHALL cite the live evidence that gated it, and the notes of a
pre-release SHALL say that live evidence was not required.

#### Scenario: Final release cites evidence
- **WHEN** a `vX.Y.Z` release is published
- **THEN** its notes name the evidence file, the Apple `container` version, the date and the live counts

#### Scenario: Pre-release
- **WHEN** a `vX.Y.ZrcN` tag is pushed
- **THEN** the GitHub Release is marked pre-release and its notes say live evidence was not required

### Requirement: Release channel
Releases SHALL be published as GitHub Releases containing the built source and
wheel distributions, and publishing to a package index SHALL be disabled unless
the owner explicitly enables it. A final tag SHALL be published only after the
release evidence check passes.

#### Scenario: Tag pushed
- **WHEN** a version tag is pushed
- **THEN** the workflow builds the distributions, runs the hosted-runner checks (including the evidence check for final tags) and attaches the files to a GitHub Release, and publishes nothing to a package index

#### Scenario: Index publishing off
- **WHEN** the owner has not enabled index publishing
- **THEN** the workflow contains no step that can publish to one

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

### Requirement: The wheel check does not depend on the working directory
`scripts/verify_wheel.py` SHALL resolve the wheel it finds to an absolute path
once, when it finds it, and every later step (the contents check, the install,
the messages) SHALL use that path. The result SHALL be the same whether `--dist`
is relative or absolute, whether the path contains spaces, and whatever the
current directory of the caller is.

#### Scenario: Relative dist from the repository root
- **WHEN** the script runs from the repository root with `--dist dist` and `dist/` holds one valid wheel
- **THEN** every check prints an `ok` line and the exit status is 0

#### Scenario: Absolute dist
- **WHEN** the script runs with `--dist` set to the absolute path of that directory
- **THEN** the output and the exit status equal those of the relative form

#### Scenario: Another working directory
- **WHEN** the script runs from a directory that is not the repository root, with a relative `--dist` that is valid from there
- **THEN** the wheel installs and the checks pass

#### Scenario: Directory with spaces
- **WHEN** the directory holding the wheel has spaces in its path
- **THEN** the wheel installs and the checks pass

#### Scenario: Resolution happens once
- **WHEN** the script is read
- **THEN** only the function that finds the wheel turns a path into an absolute one, and `install` and the checks receive that result unchanged

### Requirement: The wheel is installed in isolation and nothing is left behind
The verifier SHALL install the wheel with `pip install --no-index --no-deps`
into a new virtual environment inside a temporary directory, SHALL run every
subprocess with an environment built from scratch and a working directory
outside the checkout, SHALL import the package from the environment's
`site-packages`, and SHALL remove the temporary directory on every outcome,
including a failed check.

#### Scenario: Offline install
- **WHEN** the verifier installs the wheel
- **THEN** the `pip` command line contains `--no-index` and `--no-deps`, so no index is contacted and no dependency is taken from anywhere

#### Scenario: Nothing from the checkout
- **WHEN** the checkout's `src/` is on `PYTHONPATH` of the caller
- **THEN** the imported `container_docker_shim` still comes from the environment's `site-packages`

#### Scenario: Cleanup after a failed install
- **WHEN** `pip` exits non-zero
- **THEN** the verifier exits 3 and no `verify-wheel-*` directory remains in the temporary directory

#### Scenario: Cleanup after a failed later check
- **WHEN** the install succeeds and a later check fails
- **THEN** the verifier exits 3 and no `verify-wheel-*` directory remains

### Requirement: The wheel check names what it checked and what failed
The verifier SHALL report the wheel by absolute path in every failure that
concerns installing it, SHALL fail with one line on stderr and exit status 3
when the directory holds no wheel or more than one, and SHALL print an `ok`
line with the interpreter version and platform it ran on, so a log shows what
was verified.

#### Scenario: Install failure names the file
- **WHEN** `pip` cannot install the wheel
- **THEN** the single stderr line contains the absolute path of that wheel and the tail of `pip`'s output

#### Scenario: Missing wheel
- **WHEN** `--dist` names a directory with no `*.whl` file, or a path that does not exist
- **THEN** the verifier exits 3 with one stderr line that names the directory as given

#### Scenario: More than one wheel
- **WHEN** the directory holds two wheels
- **THEN** the verifier exits 3 naming both files and installs nothing

#### Scenario: Environment line
- **WHEN** every check passes
- **THEN** the output includes an `ok` line with the Python version of the interpreter that ran the verifier and the platform

### Requirement: The release publishes only the artifact every smoke leg installed
In `release.yml`, one job SHALL build the distributions, one `smoke` leg per
supported Python version SHALL download that uploaded artifact and run
`scripts/verify_wheel.py` on it with `--expect-version` set to the tag's
version, and the job that creates the GitHub Release SHALL depend on every
`smoke` leg, SHALL download the same artifact and SHALL NOT build again. No
`smoke` leg or dependent job SHALL be allowed to fail silently
(`continue-on-error`) or to be skipped by a condition.

#### Scenario: Build once
- **WHEN** `release.yml` is inspected
- **THEN** exactly one job runs `python -m build`, and the artifact is uploaded under one name that the `smoke` and `release` jobs download

#### Scenario: A smoke leg fails
- **WHEN** the wheel fails verification on any Python version of the matrix
- **THEN** the `release` job does not run and nothing is published

#### Scenario: The floor runner cannot provide the interpreter
- **WHEN** `actions/setup-python` cannot provide the floor version on the pinned runner image
- **THEN** that leg fails at its setup step, the `release` job does not run and nothing is published

#### Scenario: No bypass
- **WHEN** the `smoke` or `release` jobs are inspected
- **THEN** neither declares `continue-on-error` and `release` has no `if:` that can run it after a failed `needs`

#### Scenario: Same version as the tag
- **WHEN** the tag is `v0.4.0` and the wheel carries another version
- **THEN** the verifier fails with a message naming both versions and the release does not run

### Requirement: Documentation states which interpreters and runners verify the wheel
`docs/releasing.md` and `docs/testing.md` SHALL state the Python version and
the runner image of each leg that verifies the wheel, the reason the floor leg is
pinned, and how to maintain the pin when the image or the interpreter reaches
end of support. A documentation test SHALL fail when the documented floor leg
differs from the workflows.

#### Scenario: Reader checks the coverage
- **WHEN** a maintainer reads `docs/releasing.md`
- **THEN** it names the floor version with its pinned runner image and the newest version with `ubuntu-latest`, and says that the floor is Python 3.9 as long as `requires-python` says so

#### Scenario: Pin drift
- **WHEN** a workflow changes the runner of the floor leg and the documentation still names the old one
- **THEN** the documentation test fails naming both

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
