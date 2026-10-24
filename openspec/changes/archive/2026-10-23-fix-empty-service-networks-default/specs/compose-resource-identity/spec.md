## MODIFIED Requirements

### Requirement: The implicit default network exists only when a selected service uses it
`up` SHALL create, check and attach the network with key `default` only when at
least one selected service uses it, either because it declares no network, because
it declares an empty list or an empty mapping of networks (`networks: []` or
`networks: {}`), or because it names `default`. A service that names only other
networks SHALL NOT cause `<project>_default` to be created, checked or reported,
and a declared top-level `default` that no selected service uses SHALL be treated
the same way.

#### Scenario: Service without networks
- **WHEN** `up` runs for a project whose only service declares no `networks`
- **THEN** `<project>_default` is created with the project and key labels and the container attaches to it

#### Scenario: Empty list of networks
- **WHEN** `up` corre para un proyecto cuyo único servicio declara `networks: []`
- **THEN** `<project>_default` se crea con las etiquetas de proyecto y de clave, y el contenedor se adjunta a ella, igual que con el normalizador oficial (`{"default": null}`)

#### Scenario: Empty mapping of networks
- **WHEN** `up` corre para un proyecto cuyo único servicio declara `networks: {}`
- **THEN** `<project>_default` se crea y el contenedor se adjunta a ella

#### Scenario: Empty forms next to explicit networks
- **WHEN** un servicio declara `networks: []` y otro declara `networks: [back]`
- **THEN** se crean `<project>_default` y `<project>_back`, una vez cada una, y cada servicio se adjunta solo a la que usa

#### Scenario: Only an external network
- **WHEN** the only service declares `networks: [edge]` and `edge` is `external: true` and exists
- **THEN** the container attaches to `edge` and the runtime receives no `network create` call

#### Scenario: Only a managed explicit network
- **WHEN** the only service declares `networks: [back]` and `back` is managed
- **THEN** `<project>_back` is created, `<project>_default` is not, and the container attaches to `<project>_back`

#### Scenario: Mixed services
- **WHEN** one service declares no networks and another declares `networks: [back]`
- **THEN** both `<project>_default` and `<project>_back` are created, once each

#### Scenario: Explicit reference to default
- **WHEN** a service declares `networks: [default]` or `networks: [default, back]`
- **THEN** `<project>_default` is created and the service attaches to it

#### Scenario: Declared but unused default
- **WHEN** the model declares a top-level `default` network and every selected service uses other networks
- **THEN** `<project>_default` is not created and is not checked

#### Scenario: Empty form outside the selection
- **WHEN** `up api` corre, `api` usa solo `edge`, y `web`, que no está seleccionado, declara `networks: []`
- **THEN** `<project>_default` no se crea

#### Scenario: Unused default belonging to another project
- **WHEN** `<project>_default` exists with another project's label and no selected service uses `default`
- **THEN** `up` succeeds and leaves that network untouched

#### Scenario: Used default belonging to another project
- **WHEN** `<project>_default` exists with another project's label and a selected service uses `default`
- **THEN** `up` exits 1 naming the network and the owner, and creates no network, volume or container

#### Scenario: Missing external network that is needed
- **WHEN** a selected service uses an external network that does not exist
- **THEN** `up` exits 1 naming the network and the service, and creates no network, volume or container, including no `<project>_default`

#### Scenario: Teardown without a default
- **WHEN** `down` runs for a project that never created `<project>_default`
- **THEN** it exits 0 and issues no `network rm` for `<project>_default`

#### Scenario: Teardown of a default created by an earlier version
- **WHEN** `<project>_default` exists with this project's label and the model declares it managed
- **THEN** `down` removes it
