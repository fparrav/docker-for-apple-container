# Spec Delta

## ADDED Requirements

### Requirement: Read-only mounts are enforced
A mount declared read-only SHALL reject writes inside the container for named
volumes and bind mounts, in short syntax (`:ro`) and long syntax
(`read_only: true`), and the shim SHALL refuse the mount before creating the
container when the runtime cannot enforce it.
#### Scenario: Named volume, long syntax
- **WHEN** a service mounts a named volume with `type: volume` and `read_only: true` and the container tries to write a file there
- **THEN** the write fails and the volume content is unchanged
#### Scenario: Named volume, short syntax through the official normalizer
- **WHEN** a service mounts `data:/data:ro`
- **THEN** a write to `/data` fails exactly as for the long syntax
#### Scenario: Bind mount
- **WHEN** a service mounts `./config:/etc/app:ro`
- **THEN** a write to `/etc/app` fails and the host directory is unchanged
#### Scenario: Runtime cannot enforce it
- **WHEN** Apple `container` has no way to mount a named volume read-only
- **THEN** `up` exits 64 naming the service, the volume and the reason, and no container is created

### Requirement: Mount options are honoured or refused
Mount options without an Apple equivalent (`bind.propagation`,
`bind.create_host_path`, `volume.nocopy`, `volume.subpath`, `tmpfs.size` on
types the runtime lacks, `type: npipe`, unknown flags) SHALL be refused, and
Docker hints with no effect on this platform (`z`, `Z`, `cached`,
`delegated`, `rw`) SHALL be accepted and documented as no-ops.
#### Scenario: Unsupported option
- **WHEN** a service sets `volume: {nocopy: true}` on a mount
- **THEN** `up` exits 64 naming the service, the mount target and the option
#### Scenario: Platform hint
- **WHEN** a service mounts `./src:/src:cached`
- **THEN** the mount is created read-write and no warning is printed
#### Scenario: Unknown flag
- **WHEN** a short-syntax mount carries a flag the shim does not know
- **THEN** `up` exits 64 naming the flag
