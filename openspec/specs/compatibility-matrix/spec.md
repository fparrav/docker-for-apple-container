# compatibility-matrix Specification

## Purpose
Defines the verifiable record of what the Docker CLI shim supports, so that
every compatibility claim has a classification and the evidence behind it.

## Requirements

### Requirement: Machine-readable matrix
The project SHALL keep a data file listing each supported or refused Docker
command, option and Compose key with its status (SUPPORTED, PARTIAL or
UNSUPPORTED), the highest evidence level reached, and a note.

#### Scenario: Entry content
- **WHEN** a reader opens the matrix entry for `docker run --cap-drop`
- **THEN** it shows a status, the Apple `container` flag it maps to, and the evidence level

### Requirement: Evidence levels
Each entry SHALL carry one evidence level: `translated` (unit test of the
generated command), `simulated` (fake runtime), `live` (verified on Apple
`container` 1.5.0), or `conformant` (also verified against Docker Engine).

#### Scenario: Level is backed by a test
- **WHEN** an entry claims `live`
- **THEN** a live test identified by name in the entry exists, and the matrix check fails if it does not

#### Scenario: No promotion without a run
- **WHEN** a live test has never been executed for the current runtime release
- **THEN** its entries are reported at the highest level that has actually run

### Requirement: Documentation derives from the matrix
The README compatibility table SHALL be generated from, or checked against, the
matrix, and a test SHALL fail when they differ.

#### Scenario: Drift detected
- **WHEN** an option's status changes in the matrix but not in the README
- **THEN** the documentation test fails and names the option

### Requirement: Every accepted option is classified
Every `docker run`, `create`, `build` and Compose key the shim accepts or
refuses SHALL appear in the matrix.

#### Scenario: Unclassified option
- **WHEN** the translator recognizes an option that is absent from the matrix
- **THEN** the matrix check fails and names the option

### Requirement: Docker and Apple differences are stated
The matrix and README SHALL state that success on Apple `container` does not
imply compatibility with Docker Engine, and SHALL mark behaviour that differs.

#### Scenario: Known difference
- **WHEN** a reader looks up `docker export` of a running container
- **THEN** the entry states that Apple requires the container to be stopped

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
