## ADDED Requirements

### Requirement: Fake-tier tests prove resource creation from the runtime call log
A fake-tier test that claims a network or volume was not created SHALL read the
argv log of the fake runtime (the log that records every call it receives) and
SHALL also check the final state of the fake; it SHALL NOT rely on a log that
the fake does not feed for that kind of call.

#### Scenario: Unneeded network
- **WHEN** a test asserts that no network was created and the shim issues `network create` for any name
- **THEN** the test fails

#### Scenario: Helper reads real calls
- **WHEN** the helper that lists created networks runs after an `up` that creates `<project>_back`
- **THEN** it returns `["<project>_back"]` and matches the names present in the final fake state
