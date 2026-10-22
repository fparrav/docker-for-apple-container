## MODIFIED Requirements

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

## ADDED Requirements

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
