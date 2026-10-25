## MODIFIED Requirements

### Requirement: Resource attributes are not dropped
Top-level network and volume attributes that change runtime behaviour
(`driver` other than the default, `driver_opts`, `ipam`, `enable_ipv6`,
`attachable`) SHALL be refused, and `internal`, `labels` and `external`/`name`
SHALL be honoured. A top-level network is validated when a selected service uses
it, and a service that declares no `networks`, `networks: []` or `networks: {}`
uses the implicit `default` network, with the same rule the execution plan applies.
#### Scenario: Volume driver options
- **WHEN** a volume declares `driver_opts` for a network file system
- **THEN** `up` exits 64 naming the volume and the attribute, and no local volume replaces it
#### Scenario: Internal network
- **WHEN** a network declares `internal: true`
- **THEN** the project network is created host-only and `docker network inspect` reports it internal
#### Scenario: Unsupported options on default with an empty list of networks
- **WHEN** a service declares `networks: []` and the top-level `networks.default` declares `ipam`
- **THEN** `up` exits 64 with a `REFUSED:` line naming `networks.ipam` and `default`, and creates no network, volume or container
#### Scenario: Unsupported options on default with an empty mapping of networks
- **WHEN** a service declares `networks: {}` and the top-level `networks.default` declares `enable_ipv6`, a `driver_opts` entry without an Apple equivalent, or an unsupported driver
- **THEN** `up` exits 64 with a `REFUSED:` line naming the attribute and `default`, and creates no network, volume or container
#### Scenario: Supported options on default with an empty form
- **WHEN** a service declares `networks: []` or `networks: {}` and `networks.default` declares only `internal: true` and non-reserved labels
- **THEN** validation passes and `up` proceeds
#### Scenario: Explicit networks do not validate an unused default
- **WHEN** every selected service declares non-empty explicit networks and a top-level `networks.default` declares `ipam`
- **THEN** validation passes, because no selected service uses `default`
#### Scenario: Selective up
- **WHEN** `up api` or `up --no-deps api` runs, `api` declares only explicit networks, and an unselected service declares `networks: []` with an invalid `networks.default`
- **THEN** validation passes
