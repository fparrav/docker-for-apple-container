## ADDED Requirements

### Requirement: Networks follow the selected services
The networks that `up` checks and creates SHALL be those used by the selected
services and by the dependencies the selection pulls in, and by no other service
of the file.

#### Scenario: Selected service on an explicit network
- **WHEN** `up api` runs and `api` uses only `edge`, while `web`, which is not selected, uses `default`
- **THEN** `<project>_default` is not created

#### Scenario: Dependency on another network
- **WHEN** `up web` runs, `web` uses `default` and depends on `db`, and `db` uses `back`
- **THEN** both `<project>_default` and `<project>_back` are created

#### Scenario: Dependencies not requested
- **WHEN** `up --no-deps web` runs and only `db`, which is not started, uses `back`
- **THEN** `<project>_back` is not created
