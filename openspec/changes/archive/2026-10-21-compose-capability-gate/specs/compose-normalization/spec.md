# Spec Delta

## MODIFIED Requirements

### Requirement: Unknown configuration is not interpreted silently
The shim SHALL refuse every service key that is neither translated nor
classified by the capability validation, naming the service and the key, and
SHALL ignore only keys that begin with `x-`.
#### Scenario: Unknown key
- **WHEN** a service contains a key that the shim does not know
- **THEN** the shim exits 64 before creating any object, naming the service and the key
#### Scenario: Extension key
- **WHEN** a service contains a key that begins with `x-`
- **THEN** the shim ignores it without a message, as Docker Compose does
#### Scenario: Per-key waiver
- **WHEN** the user names an unknown, non-isolation key in the unenforced-options variable
- **THEN** the shim proceeds, prints a `NOT APPLIED:` line naming the service and the key, and does not interpret the key
