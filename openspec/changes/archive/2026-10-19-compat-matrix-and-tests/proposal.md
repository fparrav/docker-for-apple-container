# Proposal

## Why

The project's claims about compatibility live in prose (the README) and in unit
tests that run against a fake `container` binary. Nothing separates three
different statements that the maintainer needs to keep apart:

1. the command was translated correctly (syntactic compatibility),
2. it works on a real Apple `container` runtime (local execution),
3. the same file or image works on real Docker Engine (portability).

A passing unit test proves only the first. A new Apple `container` release is
currently validated by hand, as the 1.5.0 change showed. AI agents (Hermes,
OpenClaw and others) depend on exact output shapes and exit codes, which are
covered by two contract test files but not by a stated contract. Image
workflows for AWS (ARM64 and AMD64 builds, pushing to ECR) are not documented or
verified, and the build translator refuses or ignores options without a
verified record of why.

## What Changes

- Add a machine-readable compatibility matrix (command, option or Compose key →
  status, evidence level, notes) and make the README table and a test derive
  from it, so claims cannot drift.
- Define four test tiers and mark each test with its tier: unit, fake runtime,
  live Apple `container`, and optional Docker Engine conformance. Live and
  conformance tiers are opt-in, use disposable names and clean up, and report
  "not run" honestly.
- Add reproducible fixtures A to L (Nginx, API + PostgreSQL, + Redis, health and
  dependencies, networks and volumes, ARM64 image, AMD64 image where the runtime
  supports it, `.env`, multiple files, security restrictions, short-lived
  process, partial failure) shared by the tiers.
- Write the agent CLI contract: non-interactive use, PATH-only resolution, no
  shell-config dependence, stdin/stdout/stderr, exit codes, signal
  propagation, and the output shapes of `version`, `info`, `ps --format`,
  `inspect --format`, `logs`, `exec`, `build` and `compose`. Tools that need the
  Docker socket or Engine API are listed as incompatible.
- Verify and document OCI image workflows on 1.5.0: `build` with `--platform`,
  `--build-arg`, `--target`, labels and secrets; running ARM64 and AMD64
  images; what `image push` publishes (single platform or index); login and push
  to AWS ECR; and the limits for ECS and Fargate.
- Add a repeatable procedure and a script for validating a new Apple
  `container` release against the matrix.
- Measure wrapper overhead (startup latency, memory, per-command cost versus
  calling `container` directly) and record the numbers.

## Capabilities

### New Capabilities
- `compatibility-matrix`: the verifiable record of supported, partial and
  unsupported behaviour, with evidence levels.
- `test-tiers`: the separation of unit, fake-runtime, live and Docker
  conformance tests, and the fixtures they share.
- `agent-cli-contract`: what automated callers can rely on.
- `oci-image-workflows`: building, running and publishing OCI images for Linux
  targets.

### Modified Capabilities

None.

## Impact

- Code: a small number of test-support modules and fixtures under `tests/`, a
  matrix data file, a validation script under `scripts/` or `tests/live/`, and
  minimal changes in `cli.py` for build options that the spike shows can be
  supported.
- Docs: README (matrix table, test tiers, portability section, ECR/Fargate
  notes), a short `docs/` page with the procedure for a new release.
- CI: only the unit and fake-runtime tiers run on hosted runners. Live tests
  need macOS 26 on Apple Silicon with virtualization; the limitation and the
  manual procedure are documented, not worked around.
- Out of scope: managing AWS infrastructure, implementing the Docker Engine API
  or `/var/run/docker.sock`, and emitting Apple-specific files for third
  parties (Dockerfiles and Compose files stay standard and are never rewritten).
