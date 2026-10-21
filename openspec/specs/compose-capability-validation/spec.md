# compose-capability-validation Specification

## Purpose
Defines the validation that every Compose project passes before the shim
creates any runtime resource, so that a Compose file is either honoured with
the semantics it requests or refused with a precise, machine-recognisable
message, and never run with weaker isolation than requested.

## Requirements

### Requirement: Validation precedes every side effect
`docker compose up` and `docker compose build` SHALL validate the complete
normalized model of the services they will act on before creating a network,
volume, image build or container, and SHALL leave the runtime unchanged when
validation fails.
#### Scenario: Refusal creates nothing
- **WHEN** one service of a project requests an unsupported key and the user runs `docker compose up -d`
- **THEN** the command exits 64 and `container ls -a`, `container network list` and `container volume list` show no new object for the project
#### Scenario: Existing containers untouched
- **WHEN** the project is already running and an edited file now contains an unsupported key
- **THEN** `up` exits 64 and the running containers are neither stopped nor recreated
#### Scenario: Normalizer failure
- **WHEN** the Compose model cannot be produced
- **THEN** the command exits non-zero before any runtime command that creates an object

### Requirement: Every key has a classification
Each service key, `build` sub-key, volume or mount option and top-level
network or volume attribute SHALL be classified as SUPPORTED, PARTIAL or
UNSUPPORTED, and the classification SHALL appear in the compatibility matrix.
#### Scenario: Matrix covers the vocabulary
- **WHEN** the test suite runs
- **THEN** a test fails naming any key the validation knows that has no matrix entry
#### Scenario: Supported key
- **WHEN** a service uses only SUPPORTED keys
- **THEN** validation passes silently and `up` proceeds

### Requirement: Unknown keys are refused
A key that has no classification SHALL be refused, except keys that begin with
`x-`, which are Compose extensions and are ignored as Compose ignores them.
#### Scenario: Unknown service key
- **WHEN** a service declares a key introduced by a newer Compose release that the shim does not classify
- **THEN** `up` exits 64 naming the service and the key, and no object is created
#### Scenario: Extension key
- **WHEN** a service declares `x-owner: team-a`
- **THEN** validation passes and the key has no effect

### Requirement: All problems are reported together
Validation SHALL report every refusal found in the project in one run, each
naming the service, the key and the reason, then exit 64.
#### Scenario: Two problems
- **WHEN** service `a` sets `network_mode: none` and service `b` sets `secrets`
- **THEN** one run prints a line for each, naming `a`/`network_mode` and `b`/`secrets`, and exits 64

### Requirement: Network isolation requests are refused
`network_mode` with any value, and every other key that requests a network
namespace different from the project network, SHALL be refused, because Apple
`container` 1.5.0 has no equivalent and the service would otherwise gain
connectivity it did not request.
#### Scenario: No network
- **WHEN** a service sets `network_mode: none`
- **THEN** `up` exits 64 naming the service and `network_mode`, and the service never joins the project network
#### Scenario: Shared namespace
- **WHEN** a service sets `network_mode: host`, `service:db` or `container:x`
- **THEN** `up` exits 64 naming the value
#### Scenario: Waiver has no effect
- **WHEN** the user sets the unenforced-options variable to `network_mode` and runs `up`
- **THEN** the command still exits 64 and the message states that isolation requests cannot be waived

### Requirement: Restrictions without an equivalent are refused
Keys that restrict or isolate a container and have no Apple equivalent (for
example `pid`, `ipc`, `uts`, `cgroup_parent`, `userns_mode`, `cpu_shares`,
`cpu_quota`, `mem_reservation`, `memswap_limit`, `oom_score_adj`, `group_add`,
`device_cgroup_rules`, `runtime`, `gpus`, `volumes_from`) SHALL be refused
unless the user names that key in the unenforced-options variable.
#### Scenario: Resource limit with no equivalent
- **WHEN** a service sets `mem_reservation: 256m`
- **THEN** `up` exits 64 naming the key
#### Scenario: Explicit waiver
- **WHEN** the user names `mem_reservation` in the unenforced-options variable
- **THEN** `up` proceeds, prints a `NOT APPLIED` line for that service and key, and the limit is not set

### Requirement: Secrets and configs are refused
Service `secrets` and `configs` SHALL be refused before any object is created
until a translation that keeps their confidentiality and permissions is
verified, and the message SHALL say they are a Compose feature the shim cannot
yet honour, not a Swarm-only feature.
#### Scenario: File secret
- **WHEN** a service lists `secrets: [db_password]` backed by a local file
- **THEN** `up` exits 64 naming the service and `secrets`, and nothing is created
#### Scenario: No exposure through the environment
- **WHEN** a project defines secrets
- **THEN** the shim never copies a secret value into an environment variable, a label or its own output

### Requirement: Behavioural reductions are signalled
Keys whose omission changes behaviour but not isolation or data safety
(`restart` other than `no`, `hostname`, `domainname`, `extra_hosts`,
`stop_signal`, `stop_grace_period`, `deploy.replicas` above 1, network
aliases, `mac_address`, `links`, `logging`) SHALL be classified PARTIAL, applied
only as far as Apple allows, and reported with a `NOT APPLIED` line.
#### Scenario: Restart policy
- **WHEN** a service sets `restart: always`
- **THEN** `up` proceeds, prints `NOT APPLIED` naming the service and `restart`, and the container is created without a restart policy
#### Scenario: Default restart value
- **WHEN** a service sets `restart: "no"`
- **THEN** no line is printed because nothing is reduced

### Requirement: Unapplied options are unmistakable
Every option that is waived, or applied only in part, SHALL be reported once
per service and key on standard error as a line that starts with `NOT APPLIED:`,
SHALL NOT appear on standard output, and SHALL NOT change the command's exit
status. Refusals SHALL start with `REFUSED:`.
#### Scenario: Parseable by an agent
- **WHEN** an agent runs `docker compose up -d` for a project with a PARTIAL key
- **THEN** it can find exactly one `NOT APPLIED:` line per affected service and key on standard error, while standard output contains only Compose progress
#### Scenario: Silent when nothing is reduced
- **WHEN** every key is applied as requested
- **THEN** the output contains no `NOT APPLIED:` and no `REFUSED:` line

### Requirement: Build keys are forwarded or refused
Compose `build` SHALL forward `context`, `dockerfile`, `args`, `target`,
`labels`, `platforms`, `no_cache`, `pull`, `secrets` and `ssh: default` to the
build, and SHALL refuse every other sub-key, so that a stage, label or secret
requested for the image is never dropped.
#### Scenario: Build target
- **WHEN** a service sets `build.target: runtime` and the Dockerfile has several stages
- **THEN** the build receives the target `runtime` and the resulting image is the `runtime` stage
#### Scenario: Unsupported build key
- **WHEN** a service sets `build.cache_from`
- **THEN** `up` and `build` exit 64 naming the service and the sub-key
#### Scenario: Build secret
- **WHEN** a service sets `build.secrets` that reference a file secret
- **THEN** the build receives the equivalent `--secret` and the secret is absent from the final image

### Requirement: Resource attributes are not dropped
Top-level network and volume attributes that change runtime behaviour
(`driver` other than the default, `driver_opts`, `ipam`, `enable_ipv6`,
`attachable`) SHALL be refused, and `internal`, `labels` and `external`/`name`
SHALL be honoured.
#### Scenario: Volume driver options
- **WHEN** a volume declares `driver_opts` for a network file system
- **THEN** `up` exits 64 naming the volume and the attribute, and no local volume replaces it
#### Scenario: Internal network
- **WHEN** a network declares `internal: true`
- **THEN** the project network is created host-only and `docker network inspect` reports it internal

### Requirement: The validated set is the created set
Validation SHALL cover exactly the services that `up` would create or start,
including dependencies pulled in by the selection, and SHALL NOT refuse a
project because of a service that the command will not touch.
#### Scenario: Unselected service
- **WHEN** service `legacy` has `network_mode: host` and the user runs `up -d api`, where `api` does not depend on `legacy`
- **THEN** validation passes
#### Scenario: Selected dependency
- **WHEN** `api` depends on `db` and `db` has `secrets`
- **THEN** `up -d api` exits 64 naming `db`
