# Proposal

## Why

The local runtime is Apple `container` 1.5.0, but the shim, its fixtures and its
docs are pinned to 1.4.1, and `AGENTS.md` hardcodes that literal as a runtime
contract. Nothing has verified that the translator works on 1.5.0, so any drift
in CLI options or JSON shapes would go unnoticed until it breaks a real
workflow. The literal in `AGENTS.md` is also a second copy of a fact that
already lives in the code, which makes every runtime bump a multi-file edit.

## What Changes

- Validate the shim against the live Apple `container` 1.5.0 runtime: unit
  suite, a read-only parse matrix (version, info, ps, images, inspect, image
  inspect, port, logs, compose ps) and a lifecycle smoke test with a disposable,
  explicitly named container.
- Fix any inconsistency the validation finds in translations or strict JSON
  parsing, using the 1.5.0 schema directly and without compatibility paths.
- Advance the supported runtime constant, test fixtures and fake-binary echoes
  from 1.4.1 to 1.5.0, and update the README requirements and baseline text.
- Resolve the `container` executable from `CONTAINER_DOCKER_SHIM_CONTAINER` or,
  failing that, from `PATH`, instead of a fixed `/usr/local/bin/container`. A
  Homebrew install lives in `/opt/homebrew/bin`, so the fixed default makes the
  shim report "CLI not found" on a correctly installed 1.5.0.
- Investigate `docker images` output on 1.5.0: a live run showed `postgres:17`
  with SIZE `10.2kB` (equal to the OCI index descriptor size, not the image
  size) and an image listed as created "56 years ago" (epoch 0). Whether this is
  1.5.0 drift or an older defect is unconfirmed; fix it against the 1.5.0 JSON.
- Remove the hardcoded `1.4.1` literal from `AGENTS.md`; it will point to the
  single supported-version constant in the code as the source of truth, while
  keeping the "exactly one supported runtime, no backward compatibility" rule.
- Capture the runtime-compatibility contract as a spec so future bumps are
  validated against the same requirements.

## Capabilities

### New Capabilities
- `runtime-compatibility`: the shim supports exactly one Apple `container`
  release, locates its executable predictably, parses its JSON strictly,
  reports real image metadata, reports that release in user-facing messages and
  docs, and is validated against it before the supported version advances.

### Modified Capabilities

None. `openspec/specs/` is empty, so there is no existing capability to modify.

## Impact

- Code: `src/container_docker_shim/cli.py` (supported-version constant, binary
  resolution, image size/date translation, plus any other fixes found during
  validation); `compose.py` only if validation
  finds a 1.5.0 mismatch.
- Tests: `tests/test_cli.py`, `tests/test_compose.py`, `tests/test_inspect.py`,
  `tests/test_hermes_contract.py` (version strings in fixtures and assertions).
- Docs: `README.md` (requirements section and test baseline) and `AGENTS.md`
  (runtime contract wording).
- Out of scope: the centrally published website in the
  `appautomaton.github.io` repository, which this fork does not own.
