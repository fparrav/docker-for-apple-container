# compose-service-selection Specification

## Purpose
Defines which services `docker compose up` creates for a given selection, so
that a service is never started without the dependencies it declared and
independent services are not started by accident.

## Requirements

### Requirement: Selection includes its dependencies
`docker compose up [SERVICE...]` SHALL act on the selected services and on the
transitive closure of their `depends_on`, in dependency order, and on no other
service.
#### Scenario: Direct dependency
- **WHEN** `api` depends on `db` and the user runs `docker compose up -d api`
- **THEN** `db` is created and ready before `api` is created, and a third service `worker` that `api` does not depend on is not created
#### Scenario: Transitive dependency
- **WHEN** `web` depends on `api` and `api` depends on `db`
- **THEN** `up -d web` creates `db`, `api` and `web` in that order
#### Scenario: Whole project
- **WHEN** no service is named
- **THEN** every service of the active profiles is created, as today

### Requirement: Conditions apply to pulled-in dependencies
Dependency conditions SHALL be enforced for dependencies added by the closure
exactly as for explicitly selected services.
#### Scenario: Healthy gate on a pulled-in service
- **WHEN** `api` depends on `db` with `service_healthy` and the user runs `up -d api`
- **THEN** `api` is not created until the healthcheck of `db` has succeeded, and `up` exits non-zero if it does not
#### Scenario: Completed job
- **WHEN** `api` depends on a migration job with `service_completed_successfully`
- **THEN** the job runs to success before `api` is created, and `up` fails if it exits non-zero

### Requirement: Existing dependency containers are reconciled
A dependency that already exists SHALL be kept, started or recreated by the
same rules as any selected service, and SHALL NOT be recreated only because it
was reached through the closure.
#### Scenario: Running dependency
- **WHEN** `db` is running and unchanged and the user runs `up -d api`
- **THEN** `db` is reported up to date and left running
#### Scenario: Stopped dependency
- **WHEN** `db` exists but is stopped
- **THEN** `up -d api` starts it and waits for its condition before creating `api`

### Requirement: No dependencies on request
`docker compose up --no-deps SERVICE...` SHALL act only on the named services
and SHALL NOT wait for conditions of dependencies it did not start.
#### Scenario: No deps
- **WHEN** the user runs `up -d --no-deps api`
- **THEN** only `api` is created and no healthcheck of `db` is evaluated
#### Scenario: Missing dependency container
- **WHEN** `--no-deps` is used and `db` does not exist
- **THEN** `api` is created, and any failure to reach `db` is the application's, with a notice that dependencies were skipped

### Requirement: Optional dependencies
A `depends_on` entry with `required: false` SHALL be started when its service
is part of the plan, SHALL NOT pull its service into the plan, and SHALL NOT
fail `up` when it is absent or unhealthy, reporting a notice instead.
#### Scenario: Optional dependency absent from the selection
- **WHEN** `api` depends on `cache` with `required: false` and the user runs `up -d api`
- **THEN** `api` is created and `cache` is not
#### Scenario: Optional dependency unhealthy
- **WHEN** the optional dependency is in the plan and its healthcheck never succeeds
- **THEN** `api` is created after the wait ends, and a notice names the dependency

### Requirement: Invalid graphs are rejected before creation
Cycles, dependencies on undefined services and required dependencies in an
inactive profile SHALL be rejected with an error naming the services involved,
before any object is created.
#### Scenario: Cycle
- **WHEN** `a` depends on `b` and `b` depends on `a`
- **THEN** `up` exits 64 naming `a` and `b` and creates nothing
#### Scenario: Required dependency in an inactive profile
- **WHEN** `api` depends on `db`, `db` has `profiles: [storage]` and that profile is inactive
- **THEN** `up -d api` exits non-zero naming `db` and the profile, and creates nothing

### Requirement: Selection of unknown services
Naming a service that is not in the project SHALL fail before any object is
created.
#### Scenario: Typo
- **WHEN** the user runs `up -d apii`
- **THEN** the command exits 1 naming `apii` and creates nothing
