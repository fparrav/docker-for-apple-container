# Spec Delta

## Purpose

Defines how Compose `depends_on` conditions and healthchecks are enforced, so
that a dependent service starts only when its dependency satisfies the declared
condition, or the command fails.

## ADDED Requirements

### Requirement: Dependency conditions are enforced
The shim SHALL enforce `service_started`, `service_healthy` and
`service_completed_successfully` before starting a dependent service, and SHALL
NOT ignore a condition it cannot enforce.

#### Scenario: Service started
- **WHEN** a service depends on another with `condition: service_started`
- **THEN** it is created after the dependency's container is running

#### Scenario: Service healthy
- **WHEN** an API depends on `db` with `condition: service_healthy` and `db` has a healthcheck
- **THEN** the API container is not created until the healthcheck of `db` has succeeded

#### Scenario: Service completed successfully
- **WHEN** a service depends on a migration job with `condition: service_completed_successfully`
- **THEN** the service is created only after the job exited with code 0, and `up` fails if the job exits non-zero

#### Scenario: Healthy condition without a healthcheck
- **WHEN** a service depends on another with `condition: service_healthy` and the dependency declares no healthcheck
- **THEN** `up` exits non-zero before creating any container, naming both services

### Requirement: Healthcheck semantics
The shim SHALL evaluate healthchecks by running the declared `test` inside the
container, honouring `interval`, `timeout`, `retries`, `start_period` and
`start_interval`, with Compose's defaults when a field is omitted.

#### Scenario: Shell form and exec form
- **WHEN** a healthcheck uses `CMD-SHELL` or `CMD`
- **THEN** the shell form runs under `/bin/sh -c` and the exec form runs the argument vector directly

#### Scenario: Start period
- **WHEN** a check fails during `start_period`
- **THEN** the failure does not count toward `retries`, and a success marks the service healthy immediately

#### Scenario: Retries exhausted
- **WHEN** consecutive failures after the start period reach `retries`
- **THEN** the service is declared unhealthy, `up` exits non-zero and the output names the service and the last check output

#### Scenario: Disabled healthcheck
- **WHEN** a service sets `healthcheck.disable: true` or `test: ["NONE"]`
- **THEN** no check runs and the service is treated as having no healthcheck, so a `service_healthy` dependency on it is refused

### Requirement: Bounded waiting
Every wait performed by the shim SHALL have a finite limit and SHALL stop early
when the awaited container has exited.

#### Scenario: Default limit
- **WHEN** a healthcheck never succeeds
- **THEN** `up` stops waiting after the time derived from `start_period`, `interval`, `timeout` and `retries`, and exits non-zero

#### Scenario: Container exits while waiting
- **WHEN** the awaited container stops before becoming healthy
- **THEN** `up` stops waiting immediately, exits non-zero and prints the container's recent logs

### Requirement: Wait flag
`docker compose up -d --wait` SHALL return only when every started service is
running, and healthy where it declares a healthcheck, or fail otherwise.

#### Scenario: All services ready
- **WHEN** the user runs `docker compose up -d --wait` and all services reach running or healthy
- **THEN** the command exits 0 after that point

#### Scenario: Wait timeout
- **WHEN** the user passes `--wait-timeout 30` and a service is not ready in 30 seconds
- **THEN** the command exits non-zero and names the services that were not ready

#### Scenario: One-shot service
- **WHEN** a service is a job that exits with code 0 under `--wait`
- **THEN** the exit is not treated as a failure only if the service is a `service_completed_successfully` dependency

### Requirement: Low resource use
Waiting SHALL poll no more often than the declared `interval` (or
`start_interval` during the start period) and SHALL leave no process running
after the command returns.

#### Scenario: No residual process
- **WHEN** `up -d --wait` returns
- **THEN** no shim process and no helper process remain running
