# Spec Delta

## ADDED Requirements

### Requirement: Differing meanings are recorded
For every option that shares a name or short alias with an Apple `container`
option but means something different in Docker, the matrix SHALL record both
meanings and the decision (translated, converted, refused or labelled
extension).
#### Scenario: Short alias
- **WHEN** a reader opens the entry for `run -c`
- **THEN** it states that Docker's `-c` is `--cpu-shares`, Apple's `-c` is `--cpus`, and the shim refuses it
#### Scenario: Missing record
- **WHEN** a translated option has a short alias or Apple flag name that differs from Docker's and no record
- **THEN** the matrix check fails naming the option
