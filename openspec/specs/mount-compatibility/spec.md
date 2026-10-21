# mount-compatibility Specification

## Purpose
Defines one classification of Docker mount modes and attributes that
`docker run` and `docker compose` both obey, so a mount option is never
accepted by one and silently ignored or refused by the other.

## Requirements

### Requirement: One classification for Docker and Compose
Every mount mode and mount attribute SHALL have exactly one verdict (accepted
with effect, accepted without effect, or refused), and `docker run -v`,
`docker run --mount` and Compose volumes in short and long syntax SHALL return
the same verdict for the same option.

#### Scenario: Same verdict in both tools
- **WHEN** the same mode is given as `docker run -v ./data:/data:MODE` and as the Compose volume `./data:/data:MODE`
- **THEN** both are accepted or both exit 64 with a message that names the mode

#### Scenario: Short and long syntax agree
- **WHEN** a Compose file expresses an option once as `./data:/data:ro` and once as `{type: bind, source: ./data, target: /data, read_only: true}`
- **THEN** both produce the same Apple `--mount` argument

### Requirement: SELinux relabeling is refused everywhere
The modes `z` and `Z`, alone or combined (`ro,Z`), and the attribute
`bind.selinux` SHALL be refused with exit 64 for bind mounts and named volumes
in `docker run` and Compose, and SHALL NOT be dropped silently.

#### Scenario: Short syntax in Compose
- **WHEN** a service has `volumes: ["./data:/data:Z"]` and the user runs `docker compose up -d`
- **THEN** the command exits 64 with a `REFUSED:` line naming the service, the mount target and SELinux, and nothing is created

#### Scenario: Long syntax and named volume
- **WHEN** a service has a long-syntax bind with `bind: {selinux: z}` or the named volume `data:/d:Z`
- **THEN** the command exits 64 with the same reason

#### Scenario: Compatible with docker run
- **WHEN** the user runs `docker run -v ./data:/data:Z alpine true`
- **THEN** the command exits 64 with the same reason and no runtime call is made

#### Scenario: Compose file is not modified
- **WHEN** the refusal occurs
- **THEN** the Compose file is byte-identical afterwards and the message says the option can be removed because macOS has no SELinux

### Requirement: Read-only mounts stay read-only
`ro` and `read_only: true` SHALL make the mount read-only in the Apple
runtime, for bind mounts and named volumes in both syntaxes, and the
write protection SHALL be demonstrated by a failed write in the live tier.

#### Scenario: Both syntaxes
- **WHEN** one service mounts a volume and a bind read-only, one with `:ro` and one with `read_only: true`
- **THEN** a write to each fails inside the container, and a read-write mount of the same kind accepts the write

### Requirement: Harmless options are accepted without effect
`rw`, `cached`, `delegated` and the long-syntax `consistency` SHALL be accepted
and SHALL change nothing, because Docker Engine itself gives them no effect
outside Docker Desktop.

#### Scenario: Accepted no-op
- **WHEN** a volume uses `:cached` or `consistency: delegated`
- **THEN** the mount is created exactly as without the option and no refusal is printed

### Requirement: Unsupported options are named and refused
Any other mode or attribute, including `nocopy`, `propagation`, `subpath`,
`image` and `tmpfs` mounts inside `volumes`, and unknown values, SHALL be refused
with exit 64 naming the option and the mount target.

#### Scenario: Unknown short mode
- **WHEN** a volume uses `:weird`
- **THEN** the command exits 64 and names `weird`

#### Scenario: Unsupported nested attribute
- **WHEN** a long-syntax volume sets `volume: {nocopy: true}` or `bind: {propagation: rshared}`
- **THEN** the command exits 64 and names the attribute

### Requirement: Mounts are validated before any resource exists
`docker compose up` and `build` SHALL translate and validate the mounts of every
service they act on before creating a network, volume, image or container, and
every problem SHALL be reported together as `REFUSED:` lines.

#### Scenario: Second service has an invalid mount
- **WHEN** service `a` is valid and service `b` has an unsupported mount option
- **THEN** the command exits 64, and the runtime records no `network create`, `volume create`, `build` or `run`, including for `a`

#### Scenario: Mount path the translator cannot express
- **WHEN** a bind source contains a comma, or a target is not absolute
- **THEN** the command exits 64 with a `REFUSED:` line naming the service and path, and no traceback is printed

### Requirement: Accepted options carry evidence
The compatibility matrix and README SHALL list `z`, `Z` and `selinux` as
UNSUPPORTED, and every mount option listed as SUPPORTED SHALL cite the test
that proves it at the level it claims.

#### Scenario: Matrix after the change
- **WHEN** the matrix is rendered
- **THEN** `compose volumes.z`, `volumes.Z` and `volumes.selinux` read UNSUPPORTED with the refusal reason, and `volumes.ro` cites a live test
