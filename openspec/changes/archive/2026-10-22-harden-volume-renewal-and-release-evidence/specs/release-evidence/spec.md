## ADDED Requirements

### Requirement: Evidence names the schema it follows
Every evidence file SHALL record a schema number, and the verifier SHALL accept
only the current one. A file written under an earlier schema SHALL fail with a
message that names both numbers and says to run the validation again.

#### Scenario: Current schema
- **WHEN** the newest evidence records the current schema number
- **THEN** the verifier goes on to judge the other conditions

#### Scenario: Earlier schema
- **WHEN** the newest evidence records schema 1 and the current schema is 2
- **THEN** the verifier fails naming both numbers and says the validation must be run again

## MODIFIED Requirements

### Requirement: Evidence is bound to the tested code
The tree hash SHALL be computed from the git tree identifiers of `src/`, `scripts/`
and `tests/` and from the git object identifier of `pyproject.toml`, so that a
commit that changes only documentation, workflows or the evidence files themselves
keeps the evidence valid, and any change to the code, the tests or the packaging
configuration invalidates it.

#### Scenario: Documentation commit
- **WHEN** a commit after the evidence changes only `README.md` and adds the evidence file
- **THEN** the tree hash at the new commit equals the recorded one

#### Scenario: Code commit
- **WHEN** a commit after the evidence changes a file under `src/`
- **THEN** the tree hash differs from the recorded one

#### Scenario: Packaging commit
- **WHEN** a commit after the evidence changes `pyproject.toml`, for example the entry point, the Python floor or the build backend
- **THEN** the tree hash differs from the recorded one and the verifier fails naming both hashes

#### Scenario: Uncommitted packaging change
- **WHEN** `pyproject.toml` differs from `HEAD` and the validation is asked to write evidence
- **THEN** the command refuses and names `pyproject.toml` as the dirty input
