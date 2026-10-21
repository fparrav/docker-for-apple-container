# Design

## Context

The supported release is defined once, as `SUPPORTED_CONTAINER_VERSION` in
`src/container_docker_shim/cli.py`, and interpolated into every strict-parsing
error and the help text. The string "1.4.1" is duplicated in four test files
(fake-binary echoes and assertions), in three places in `README.md`, and in
`AGENTS.md`. The previous bump (4b743a5) shows the established method: run the
unit suite, run a live read-only parse matrix, run a disposable lifecycle smoke,
and only then change the constant. Today the live runtime reports 1.5.0 and the
1.4.1-pinned unit suite passes with fakes, which proves nothing about 1.5.0
because the fakes encode 1.4.1 payloads. See proposal.md for motivation.

## Goals / Non-Goals

**Goals:**
- Establish by live evidence whether 1.5.0 payloads and CLI options differ from
  what the translator expects, and fix whatever differs.
- Keep one source of truth for the supported version: the code constant.
- Leave the runtime contract rules (exactly one release, strict schema, no
  shim-owned state) intact.

**Non-Goals:**
- Supporting more than one release, or loosening the version check into a range
  or "latest" policy.
- Refreshing the centrally published website, which is owned by another repo.
- Adding new Docker command translations unrelated to 1.5.0 drift.

## Decisions

**Validate before bumping.** Run the live matrix first, with the constant still
at 1.4.1, and capture real 1.5.0 JSON for comparison. This separates "payload
changed" findings from "string changed" edits. Alternative: bump first and let
failures surface; rejected because failures would then be confounded with the
version string and the error messages would already claim 1.5.0.

**Drift is found by comparing captured JSON with fixtures.** Capture
`container ls/inspect/image ls/image inspect --format json` output from the live
runtime, and diff its key sets against what the parsers require and what test
fixtures contain. Update fixtures to the real 1.5.0 shape where they differ.
Alternative: only rely on the existing unit tests; rejected as above.

**AGENTS.md drops the literal, the code keeps it.** AGENTS.md will say the
shim supports exactly the release declared by `SUPPORTED_CONTAINER_VERSION` and
that the constant, fixtures and README advance together after validation. This
removes the duplicated fact the user wants gone while preserving the strict
single-release rule. Alternative: delete the runtime-version rule entirely;
rejected because the code would still enforce one release and the guidance
would then contradict it. Because `AGENTS.md` is tracked upstream, the edit
shows as a working-tree change even though it is listed in the local git
exclude; it must not be committed to this fork's PR branch unless intended.

**Resolve the executable from the variable, then `PATH`.** Use
`CONTAINER_DOCKER_SHIM_CONTAINER` when set and `shutil.which("container")`
otherwise, failing with an error that names both. Alternative: keep the fixed
default and rely on the variable; rejected because a default Homebrew install
(`/opt/homebrew/bin`) then fails with "CLI not found" without configuration.
Alternative: probe a list of known install paths; rejected as guessing, which
the runtime contract avoids.

**Confirm the image-metadata cause before fixing it.** A live `docker images`
on 1.5.0 showed `postgres:17` at 10.2kB, equal to the OCI index
`descriptor.size` of 10237, and a "56 years ago" creation time. Task 1.3
compares the captured `image ls --format json` payload with what the parser
reads, to decide between a wrong field and a missing-date fallback. Alternative:
patch the display only; rejected until the cause is known.

**Lifecycle smoke uses one explicitly named disposable container.** It is
removed in the same run, and no global prune is used, per the repository
verification rules.

## Risks / Trade-offs

- [1.5.0 changed a JSON shape in a command the read-only matrix does not cover]
  → Include port, logs and compose ps in the matrix, and treat any strict-parse
  error during the lifecycle smoke as a finding to fix.
- [Fixtures edited to match 1.5.0 hide a real regression] → Derive fixtures from
  captured live output, not from the translator's expectations.
- [The `docker images` defect predates 1.5.0] -> Record that in the findings;
  the fix targets the 1.5.0 schema and is not framed as backward compatibility.
- [Excluding `AGENTS.md` locally hides an edit to a tracked file] → The exclude
  has no effect on tracked files; the edit stays visible in `git status`, and
  that is surfaced to the user rather than hidden with skip-worktree.
