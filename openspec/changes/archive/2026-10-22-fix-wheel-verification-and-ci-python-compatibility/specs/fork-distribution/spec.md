## ADDED Requirements

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
