# Spec Delta

## Purpose

Defines how Compose services find each other by service name on Apple
`container`, without changing the macOS host's network configuration.

## ADDED Requirements

### Requirement: Resolution by service name
Every service SHALL be able to reach every other service of the same project on
a shared network by its Compose service name.

#### Scenario: API reaches the database
- **WHEN** an API service and a PostgreSQL service named `db` share a network and the API connects to host `db` on port 5432
- **THEN** the connection succeeds on the first start of the stack

#### Scenario: Name is resolvable inside the container
- **WHEN** the user runs `docker compose exec api getent hosts db`
- **THEN** it prints the current address of the `db` container

### Requirement: Resolution survives restarts
Name resolution SHALL continue to work after `docker compose restart`,
`docker restart` of a project container and `docker compose stop` followed by
`start`, even when the runtime assigns a different address.

#### Scenario: Database restarted with a new address
- **WHEN** `db` is restarted and receives a different IP address
- **THEN** the API container resolves `db` to the new address without being recreated

#### Scenario: Idempotent injection
- **WHEN** the resolution update runs repeatedly
- **THEN** the container's hosts file contains exactly one line per name and unrelated lines are untouched

### Requirement: Host configuration is not modified
The shim SHALL NOT modify the macOS host's `/etc/hosts` or its global DNS
configuration, and SHALL NOT run a resident DNS service.

#### Scenario: Host files unchanged
- **WHEN** a project is started, restarted and removed
- **THEN** the host's `/etc/hosts` and resolver configuration are byte-identical before and after

### Requirement: Images without a shell
The shim SHALL provide name resolution for images that lack `/bin/sh`, or SHALL
state clearly that a service could not be updated.

#### Scenario: Distroless service
- **WHEN** a service uses an image without a shell
- **THEN** the hosts file is updated without a shell in the image, or a warning names the service and says its peers cannot be resolved

### Requirement: Native mechanisms are preferred
If the runtime provides name resolution between containers that satisfies the
requirements above, the shim SHALL use it instead of editing hosts files.

#### Scenario: Evaluation is recorded
- **WHEN** the change is completed
- **THEN** the findings document states, with evidence from the live runtime, whether a native mechanism for container name resolution exists and why it was or was not adopted

### Requirement: Host reachability
Containers SHALL be able to reach the macOS host through
`host.docker.internal`.

#### Scenario: Host name
- **WHEN** a service resolves `host.docker.internal`
- **THEN** it receives the gateway address of its network
