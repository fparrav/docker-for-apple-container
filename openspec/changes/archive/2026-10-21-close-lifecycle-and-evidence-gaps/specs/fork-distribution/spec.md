## MODIFIED Requirements

### Requirement: Semantic versioning and release notes
Releases SHALL use semantic versions created from Git tags. A tag of the form
`vX.Y.Z` SHALL be a final release; a PEP 440 pre-release tag (`rc`, `a`, `b`, `dev`)
SHALL be a pre-release published as a GitHub pre-release. Each release note SHALL
state the supported Apple `container` version, the breaking changes, and which
test tiers were run and which were not run. The notes of a final release SHALL cite
the live evidence that gated it, and the notes of a pre-release SHALL say that live
evidence was not required.

#### Scenario: Release note content
- **WHEN** a release is published
- **THEN** its notes include the supported runtime version and a tier table with passed, failed, skipped and not run counts

#### Scenario: Final release cites evidence
- **WHEN** a `vX.Y.Z` release is published
- **THEN** its notes name the evidence file, the Apple `container` version, the date and the live counts

#### Scenario: Pre-release
- **WHEN** a `vX.Y.ZrcN` tag is pushed
- **THEN** the GitHub Release is marked pre-release and its notes say live evidence was not required

#### Scenario: Runtime evidence missing
- **WHEN** the hosted run did not execute the live tier
- **THEN** the tier table says "live tier: not run" and the live evidence section is the only claim about live results

### Requirement: Release channel
Releases SHALL be published as GitHub Releases containing the built source and
wheel distributions, and publishing to a package index SHALL be disabled unless
the owner explicitly enables it. A final tag SHALL be published only after the
release evidence check passes.

#### Scenario: Tag pushed
- **WHEN** a version tag is pushed
- **THEN** the workflow builds the distributions, runs the hosted-runner checks (including the evidence check for final tags) and attaches the files to a GitHub Release, and publishes nothing to a package index

#### Scenario: Index publishing off
- **WHEN** the owner has not enabled index publishing
- **THEN** the workflow contains no step that can publish to one
