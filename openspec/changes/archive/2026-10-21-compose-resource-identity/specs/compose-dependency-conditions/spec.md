# Spec Delta

## ADDED Requirements

### Requirement: Required flag
The shim SHALL read `required` on each `depends_on` entry, defaulting to
`true`, and SHALL apply the semantics of `compose-service-selection` for
`required: false`.
#### Scenario: Default
- **WHEN** a `depends_on` entry has no `required` key
- **THEN** it is treated as required and a missing or failing dependency fails `up`
#### Scenario: Optional healthy gate
- **WHEN** an optional dependency declares `condition: service_healthy` and has no healthcheck
- **THEN** `up` prints a notice and does not fail, because the dependency is not required
