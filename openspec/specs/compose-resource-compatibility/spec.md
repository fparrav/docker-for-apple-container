# compose-resource-compatibility Specification

## Purpose
Defines how top-level volume and network declarations and `network_mode` that
Apple `container` cannot express are translated, noted or refused, and how
refusals explain what to do instead.

## Requirements

### Requirement: Local bind volumes are translated
A top-level volume whose driver is `local` (or unset) and whose `driver_opts` are
`type: none`, `o` made only of `bind`, `ro` and `rw` with `bind` present, and an
absolute `device`, SHALL be mounted as a bind mount of that path, SHALL NOT create
a runtime volume, and `ro` SHALL make the mount read-only.

#### Scenario: Bind idiom
- **WHEN** a volume declares `driver_opts: {type: none, o: bind, device: /srv/data}` and a service mounts it at `/data`
- **THEN** the container sees the host directory `/srv/data` at `/data` and no volume named after the key exists

#### Scenario: Read-only
- **WHEN** the options are `o: bind,ro`
- **THEN** a write to the mount fails with a read-only file system error

#### Scenario: Missing device
- **WHEN** the device directory does not exist on the host
- **THEN** the service is not started and the error names the path, as for any bind source

#### Scenario: Down -v
- **WHEN** the user runs `docker compose down -v`
- **THEN** the host directory is untouched

### Requirement: Other volume driver options stay refused and are named
Any other `driver_opts` key or value on a volume (another `type`, a relative or
missing `device`, an unlisted `o` flag) SHALL be refused with exit 64 before
anything is created, and the `REFUSED:` line SHALL name the volume, the option
and the reason.

#### Scenario: NFS volume
- **WHEN** a volume declares `type: nfs`
- **THEN** `up` exits 64 naming the volume and `type: nfs`

#### Scenario: Relative device
- **WHEN** a volume declares `type: none`, `o: bind` and `device: ./data`
- **THEN** `up` exits 64 naming the volume and that `device` must be absolute

### Requirement: Bridge name option is not applied
The network option `com.docker.network.bridge.name` SHALL be reported as `NOT
APPLIED:` on standard error and SHALL NOT stop the command; every other network
`driver_opts` key SHALL stay refused and be named in the message.

#### Scenario: Bridge name only
- **WHEN** a network sets only `com.docker.network.bridge.name`
- **THEN** `up` prints a `NOT APPLIED:` line naming the network and continues

#### Scenario: Another option
- **WHEN** a network also sets `com.docker.network.driver.mtu`
- **THEN** `up` exits 64 naming the network and that option

### Requirement: network_mode stays refused with an actionable message
`network_mode` SHALL remain refused with exit 64 and SHALL NOT be waivable, and
the message SHALL be specific to the value: `host`, `none`, `service:<name>` and
`container:<name>` each say what the runtime cannot do and what to use instead.

#### Scenario: Host networking
- **WHEN** a service sets `network_mode: host`
- **THEN** `up` exits 64 and the message suggests published `ports` and `host.docker.internal`, creating nothing

#### Scenario: Waiver attempt
- **WHEN** `network_mode` is named in `CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED`
- **THEN** it is still refused

### Requirement: Evidence for new acceptances
Every key the compatibility matrix newly lists as supported or partial by this
capability SHALL name a test, and a `live` or `conformance` claim SHALL name a
test that exists.

#### Scenario: Matrix rows
- **WHEN** the matrix is validated
- **THEN** the anonymous volume and local bind volume rows exist with their tests

### Requirement: Recursive bind is refused and points to bind
The `rbind` flag in the `o` option of a local bind volume SHALL be refused with
exit 64 before anything is created, alone or combined with other flags, and the
`REFUSED:` line SHALL name the volume and the option and SHALL tell the user to
use `bind`. The documentation and the compatibility matrix SHALL NOT list `rbind`
as accepted.

#### Scenario: rbind alone
- **WHEN** a volume declares `type: none`, `o: rbind` and an absolute `device`
- **THEN** `up` exits 64 naming the volume and `o: rbind`, suggests `o: bind`, and creates nothing

#### Scenario: rbind with other flags
- **WHEN** the options are `o: rbind,ro`
- **THEN** `up` exits 64 with the same message

#### Scenario: Documentation
- **WHEN** the README or the matrix note describes the accepted `o` values
- **THEN** they list `bind`, `ro` and `rw` and do not list `rbind`
