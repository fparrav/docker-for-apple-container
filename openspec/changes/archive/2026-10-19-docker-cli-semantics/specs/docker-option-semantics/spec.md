# Spec Delta

## Purpose

Defines that every Docker option the shim accepts keeps Docker's meaning on
Apple `container`, or is refused, so that a name or short alias shared by the
two CLIs never makes a command do something different from what Docker does.

## ADDED Requirements

### Requirement: Same meaning or refusal
The shim SHALL translate a `docker run`, `create` or `build` option only when
its documented Docker meaning and the meaning of the Apple `container` 1.5.0
option it maps to are equivalent, and SHALL otherwise refuse it with exit
status 64 naming the option and the difference.
#### Scenario: Equivalent option
- **WHEN** the user runs `docker run --cap-drop ALL alpine true`
- **THEN** the capability is dropped in the container
#### Scenario: Divergent option
- **WHEN** an option has the same name in both CLIs but a different meaning
- **THEN** the command exits 64 and the message states both meanings
#### Scenario: Audit stays current
- **WHEN** the test suite runs
- **THEN** it fails naming any translated option whose short alias or Apple flag name differs from Docker's and that is not recorded in the semantics audit

### Requirement: CPU shares are not CPU limits
`-c` and `--cpu-shares` SHALL be refused for `docker run`, `docker create` and
`docker build`, with a message that `--cpus` is the CPU limit option, because
Docker's `-c` sets a relative weight and Apple's `-c` sets a CPU count.
#### Scenario: Short alias
- **WHEN** the user runs `docker run -c 2 alpine true`
- **THEN** the command exits 64, no container is created, and the message names `--cpu-shares` and suggests `--cpus`
#### Scenario: Build alias
- **WHEN** the user runs `docker build -c 512 .`
- **THEN** the command exits 64 and no build starts
#### Scenario: Explicit limit still works
- **WHEN** the user runs `docker run --cpus 2 alpine true`
- **THEN** the container runs with two CPUs

### Requirement: Fractional and bare numeric limits follow Docker
`--cpus` values SHALL be applied as given or refused when Apple cannot
represent them, never rounded silently, and a `--memory` value without a unit
SHALL be read as bytes as Docker does, converted to Apple's units or refused
when it is not a whole number of MiB.
#### Scenario: Fractional CPUs
- **WHEN** the user runs `docker run --cpus 0.5 alpine true` and Apple 1.5.0 accepts only whole CPUs
- **THEN** the command exits 64 naming the accepted values
#### Scenario: Bare memory number
- **WHEN** the user runs `docker run -m 536870912 alpine true`
- **THEN** the container is limited to 512 MiB, and `-m 512` is refused as less than the runtime's granularity
#### Scenario: Unit letters
- **WHEN** the user runs `docker run -m 256m alpine true`
- **THEN** `docker inspect` reports 256 MiB

### Requirement: Docker volume syntax is classified by its source
In `docker run -v SOURCE:TARGET[:MODE]`, a source that is an absolute path, begins
with `.` or `~`, or contains a slash SHALL be a bind mount, and any other source
SHALL be a Docker named volume created on demand under that name; modes `ro`
and `rw` SHALL be honoured and unknown modes refused.
#### Scenario: Named volume
- **WHEN** the user runs `docker run -v pgdata:/var/lib/data alpine sh -c 'echo x > /var/lib/data/f'`
- **THEN** a volume named `pgdata` exists afterwards and holds the file, and no host directory called `pgdata` is created
#### Scenario: Read-only named volume
- **WHEN** the user runs `docker run -v pgdata:/data:ro alpine sh -c 'echo x > /data/f'`
- **THEN** the write fails, or the command is refused with exit 64 if Apple cannot enforce read-only on volumes
#### Scenario: Bind mount
- **WHEN** the user runs `docker run -v ./src:/src alpine ls /src`
- **THEN** the container sees the host directory
#### Scenario: Unknown mode
- **WHEN** the mode is `nocopy`
- **THEN** the command exits 64 naming the mode

### Requirement: Mount dialect follows Docker
`docker run --mount` SHALL accept Docker's key names (`type`, `source`/`src`,
`target`/`destination`/`dst`, `readonly`/`ro`) and types `bind`, `volume` and
`tmpfs`, translating them to Apple's mount syntax, and SHALL refuse every other
key or type.
#### Scenario: Aliases
- **WHEN** the user runs `docker run --mount type=volume,src=data,dst=/data,ro alpine true`
- **THEN** a volume `data` is mounted read-only at `/data`
#### Scenario: Unsupported key
- **WHEN** the user passes `bind-propagation=rshared`
- **THEN** the command exits 64 naming the key

### Requirement: Network modes follow Docker
`--network none`, `host` and `container:NAME` SHALL be refused naming the
mode, `bridge` and `default` SHALL attach the container to Apple's default
network, and any other value SHALL be passed as the name of an existing network.
#### Scenario: Host network
- **WHEN** the user runs `docker run --network host alpine true`
- **THEN** the command exits 64 and no container is created
#### Scenario: Default bridge
- **WHEN** the user runs `docker run --network bridge alpine true`
- **THEN** the container attaches to the default network
#### Scenario: Named network that does not exist
- **WHEN** the network does not exist
- **THEN** the command exits non-zero with the runtime's message and no container is left behind

### Requirement: Runtime selection is not an OCI runtime
`--runtime` SHALL be refused, because Docker's option chooses an OCI runtime
(`runc`) and Apple's chooses a container runtime handler.
#### Scenario: OCI runtime name
- **WHEN** the user runs `docker run --runtime runc alpine true`
- **THEN** the command exits 64 and no container is created

### Requirement: Apple-only options are labelled extensions
Options that exist only in Apple `container` (`--arch`, `--no-dns`,
`docker build --cpus`) SHALL stay available, SHALL be marked as extensions in
the matrix and README, and SHALL NOT be described as Docker-compatible.
#### Scenario: Matrix
- **WHEN** a reader opens the entry for `run --no-dns`
- **THEN** it states that Docker has no such option and that scripts using it will not run on Docker

### Requirement: No-op options have no observable effect
An option SHALL be accepted as a no-op only when ignoring it cannot change an
observable result, and the matrix SHALL state why.
#### Scenario: Build clean-up flags
- **WHEN** the user passes `docker build --rm=false .`
- **THEN** the build runs, and the matrix explains that Apple builds leave no intermediate containers so both values give the same result
#### Scenario: Option with an effect
- **WHEN** an option changes behaviour in Docker and has no Apple equivalent
- **THEN** it is refused, not accepted as a no-op

### Requirement: Signals use Docker names
`docker kill -s` and `docker stop -s` SHALL accept signal names with or without
the `SIG` prefix and numbers, and SHALL deliver the same signal.
#### Scenario: Named signal
- **WHEN** the user runs `docker kill -s SIGTERM c` or `-s TERM` or `-s 15`
- **THEN** the container's main process receives SIGTERM in each case
#### Scenario: Unknown signal
- **WHEN** the signal is not valid
- **THEN** the command exits non-zero with a message naming the signal
