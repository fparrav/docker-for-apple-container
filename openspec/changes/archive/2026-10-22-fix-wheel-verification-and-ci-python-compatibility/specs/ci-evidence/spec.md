## ADDED Requirements

### Requirement: The Python floor is checked on a pinned runner image
Every workflow job that runs on the Python floor declared by `requires-python`
SHALL select an explicit, versioned runner image (for example `ubuntu-24.04`),
never a moving `-latest` label, and that image SHALL be one for which the
`actions/python-versions` manifest lists that interpreter. The newest Python
release SHALL be checked in a separate leg of the same matrix on
`ubuntu-latest`. The floor leg SHALL NOT be dropped or moved to a moving label
to make a build pass.

#### Scenario: Floor leg pinned
- **WHEN** the `wheel` job of `ci.yml` and the `smoke` job of `release.yml` are inspected
- **THEN** the leg whose Python equals the floor in `pyproject.toml` names a runner that does not end in `-latest`, and `runs-on` takes it from the matrix

#### Scenario: Modern leg moves with the label
- **WHEN** `ubuntu-latest` changes to a newer Ubuntu release
- **THEN** the `3.x` leg runs on it unchanged and any incompatibility shows in that leg alone

#### Scenario: Floor removed
- **WHEN** a change removes the floor leg from either matrix
- **THEN** the workflow test fails naming the missing floor version

#### Scenario: Moving label on the floor
- **WHEN** a change sets the floor leg's runner to `ubuntu-latest`
- **THEN** the workflow test fails naming the leg

#### Scenario: Interpreter unavailable
- **WHEN** the pinned image no longer offers the floor interpreter
- **THEN** the leg fails visibly at `Set up Python`, and the response is a dedicated change that moves the pin or raises `requires-python`, not a silent skip

#### Scenario: Both workflows agree
- **WHEN** `ci.yml` and `release.yml` are inspected
- **THEN** their wheel matrices list the same legs (Python and runner)

### Requirement: Matrix changes keep the trust boundary
A change to the wheel matrix SHALL NOT add a third-party action, a self-hosted
runner, a credential or a permission, SHALL keep every third-party action pinned
to a full commit identifier, and SHALL keep `contents: read` for the jobs that
build or smoke-test the wheel.

#### Scenario: Pins and permissions unchanged
- **WHEN** the matrix is changed
- **THEN** `tests/test_workflows.py` still passes its pin, permission and self-hosted checks, and only the `release` job holds `contents: write`

#### Scenario: Matrix value used as a shell command
- **WHEN** the workflows are inspected
- **THEN** `matrix.os` appears only in `runs-on` and `matrix.python` only in `python-version` and the job name, never inside a `run:` line
