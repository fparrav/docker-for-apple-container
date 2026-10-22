# Design

## Context

- `cmd_up` reconciles per service: `_project_containers` gives one row per
  service (`by_service`), `compose_reconcile.decide` returns `keep`, `start`,
  `recreate` or `create`, and a dependency-driven recreate can promote a `keep`
  to `recreate`. `_create_service` then emits mounts through `_emit_planned`,
  which calls `_ensure_anonymous_volume` once per volume name
  (`ensured_volumes`). Today that helper returns at once if `volume inspect`
  succeeds, so a volume survives `down` and the next `up` reuses it. See
  `proposal.md` for why that differs from Docker.
- Anonymous volumes are named `<project>_<service>_anon-<10hex>` and labelled
  `com.docker.compose.project`, `.service` and `.anonymous`. `down -v` removes
  them through `_runtime_volumes`.
- Apple `container` 1.5.0 refuses to remove a volume that a container uses
  (stopped or not), so a removal failure is a reliable "in use" signal.
- `compose_resource_compat.local_bind` accepts the `o` tokens `bind`, `rbind`,
  `ro`, `rw` and returns a `recursive` flag nothing reads outside that module.
- `scripts/test_report.py` assigns a tier to each module through a module-level
  `TIER` and reports five outcomes; `compute_exit_status` is 1 only on failed
  tests. `tests/tiers.py` exposes the `live_tier` and `conformance_tier`
  decorators.
- `scripts/validate-runtime.py` already returns `ran` and a per-test outcome map
  from `_run_live` and exits 3 when the live tier did not fully run.
- `release.yml` runs on tags, in `checks` (read-only) then `release`
  (`contents: write`). `runtime.yml` needs a self-hosted runner that does not
  exist.

## Goals / Non-Goals

**Goals:**
- After `down` and `up`, an anonymous volume holds the image's content again,
  and the same sequence is asserted on Apple `container` and on real Docker.
- A required tier that did not run cannot pass CI, with no number written in a
  workflow.
- A final release cannot be published without recent, complete live evidence
  for the exact code and runtime it ships, using only hosted runners.

**Non-Goals:**
- Verifying `rbind` semantics, copy-up for named volumes, a self-hosted runner,
  cryptographic signing of evidence, or evidence for pre-releases.
- Changing which commands accept `-V`: only `up`.

## Decisions

### D1. Create versus recreate is decided in `cmd_up`, not in the helper

`cmd_up` already knows the action. It passes `renew_anonymous: bool` down to
`_create_service` and on to `_ensure_anonymous_volume`:

| action | `-V` | anonymous volume |
| --- | --- | --- |
| `keep`, `start` | any | not touched (the container still has it) |
| `recreate` | no | reused, never reseeded |
| `recreate` | yes | removed, created, seeded |
| `create` | any | removed if present (residue), created, seeded |

The row passed to `decide` is `by_service.get(service)`, so "no row" is exactly
"no container of this service exists", which also covers a container deleted
outside Compose. `_remove_containers([row])` runs before the volume work, so a
recreated container no longer holds its volume when `-V` removes it.
*Alternative: remove anonymous volumes in `down`.* Rejected: `down` without `-v`
must keep volumes (Docker's `down` also keeps named volumes, and the existing
requirement says so), and the volume is only a problem once the next `up` creates
a container. Doing the work in `up` also cleans volumes left by the old shim
version and by an interrupted `down`.

### D2. Residue removal checks labels before deleting

`_discard_anonymous_volume(volume)` inspects the volume, requires the three
labels to match the project and service, and only then runs `volume rm`. A volume
with the deterministic name and without them stops the command (exit 1) naming it.
A failed `rm` stops the command naming the volume and the service. Nothing is
removed by glob or prefix, in line with the project rule against global prune.
`ensured_volumes` still prevents handling a name twice in one run.

### D3. `up -V` is a boolean flag handled with the existing flag parser

`-V` and `--renew-anon-volumes` join the `bools` of the `up` branch. It is not
an alias for `--force-recreate` and does not recreate anything by itself, which
matches Docker's documented meaning ("Recreate anonymous volumes instead of
retrieving data from the previous containers"). `--dry-run up -V` already stops
before any effect, so dry run needs only to accept the flag.

### D4. One lifecycle sequence, two engines

The existing live and conformance tests for fixture `s-anonymous-volume` keep
their copy-up checks and gain the same tail, in this order: write a marker;
`up -d` with `REV=2` (recreate) keeps it; `up -d -V` with `REV=3` drops it and
copy-up is back; write a marker again; `down`; `up -d`; the marker is gone. The
conformance test loses the comment that says the shim differs from Docker. No new
fixture is needed because `REV` already forces a recreate. The `lost+found` rule
stays: live allows it, conformance expects an empty listing.

### D5. `rbind` is refused in the pure module

`local_bind` treats `rbind` like any unlisted token: the `Problem` text becomes
"`rbind` is not supported; use `bind`" and the accepted set shrinks to `bind`, `ro`,
`rw`. The unused `recursive` field is removed. The capability gate and the
translator both call `local_bind`, so one change covers `up`, `--dry-run` and the
gate. Matrix note, README (and its rendered block) and the tests that expected
`rbind` are updated.
*Alternative: keep accepting it and add a live test.* Rejected: Docker Desktop
and Apple both mount through virtiofs, where the recursive flag has no observable
effect that a test could separate from `bind`, so a test would pass either way.

### D6. `--require` compares records with an independent count

`test_report.py --require live,conformance` (comma list, opt-in tiers only,
validated by argparse) runs after the normal report is built and written. For each
required tier it computes:

- `records`: the test records of that tier from the run;
- `declared`: an AST scan of `tests/test_*.py` counting `test_*` functions
  decorated with `tiers.live_tier(...)` or `tiers.conformance_tier(...)` in
  modules whose module-level `TIER` is that tier (the scan matches the decorator
  name, so adding a class or a test needs no edit; `tests/test_tiers.py`
  decorates throwaway functions in a unit-tier module and must not count). It is independent of the unittest loader, so a module that
  stops contributing tests cannot hide.

The requirement is met only when `declared > 0`, every record is `passed`, and
`passed >= declared`. Otherwise stderr lists the first few offending tests with
outcome and reason, and the exit status is 3. A failure keeps exit status 1
because it is already the more severe signal; `--require` adds 3 only when no
test failed. The same scan is exposed as a function, `declared_tests(tier)`, so
`validate-runtime.py` can reuse it.
*Alternative: a fixed count in the workflow* (as the user ruled out): it
rots on every added test. *Alternative: trust the records alone:* it cannot see
tests that were never collected.

One live-tier caveat: `test_live_fixtures.py` calls `skipTest` when an image
cannot be pulled, so `--require live` would fail there. That is the intended
strictness for evidence, and CI never requires `live`.

### D7. Fixture coverage is a unit test over names, not a run

A unit test in `tests/test_conformance_fixtures.py`'s sibling (a
non-opt-in module) reads the first line of each fixture's `compose.yaml` under
`tests/fixtures/compat/`, keeps those that list `conformance`, and asserts that
the fixture directory name appears in `tests/test_conformance_fixtures.py`
(through the `FIXTURE_*` constants that the file already defines). It does not
count tests. `ps-reference` has no header and is excluded by that rule.

### D8. Evidence file, hash and verifier

**Where.** `docs/evidence/runtime-<container-version>-<YYYYMMDDTHHMMSSZ>.json`.
The directory is committed, files are small, and history stays in git.

**Content (schema 1).**

```json
{
  "schema": 1,
  "container_version": "1.5.0",
  "generated_at": "2026-10-21T14:03:00Z",
  "commit": "<HEAD sha, informational>",
  "trees": {"src": "<sha>", "scripts": "<sha>", "tests": "<sha>"},
  "tree_hash": "<sha256 of the three lines 'src <sha>' ... joined by newlines>",
  "live": {"ran": 0, "passed": 0, "failed": 0, "skipped": 0, "not_run": 0,
           "declared": 0},
  "result": "passed"
}
```

**Hash.** `git rev-parse HEAD:src`, `HEAD:scripts`, `HEAD:tests` give tree
identifiers that change exactly when any file under the directory changes. A
commit that only touches the README, `docs/` (including `docs/evidence/`),
`openspec/`, workflows or `pyproject.toml` leaves the hash alone, which is what
lets the evidence be committed after the run and before the tag. Hashing the
three tree identifiers with sha256 gives one value to compare and display. The
verifier recomputes the same value from the checked-out tag with
`git rev-parse HEAD:<dir>`; the release workflow's `checks` job already checks
out the tagged commit.

**Producing it.** `--evidence` requires a full run: it is an error with
`--skip-live`; it refuses when `git status --porcelain -- src scripts tests` is
not empty (untracked files count) because the hash comes from `HEAD`, not the
working tree; and it writes only when the existing verdict is success *and*
`declared == ran == passed` with zero failed, skipped and not run. `declared`
comes from the D6 scan. A failed or partial run writes nothing.

**Verifying.** `scripts/verify_release_evidence.py --tag <tag>`:

1. Classify the tag: `^v\d+\.\d+\.\d+$` is final; `^v\d+\.\d+\.\d+(a|b|rc)\d+$`
   and `^v\d+\.\d+\.\d+\.dev\d+$` are pre-releases; anything else fails.
2. Pre-release: print that evidence is not required and exit 0.
3. Final: load every `docs/evidence/*.json`, pick the newest `generated_at`,
   and check in order: schema and field types; counts consistent
   (`ran == passed + failed`, `skipped` and `not_run` in the record);
   `result == passed`; `container_version == SUPPORTED_CONTAINER_VERSION` (read
   from `cli.py` with the same regex `validate-runtime.py` uses, not imported, so
   the script has no package dependency); `tree_hash` equals the recomputed one;
   age at most `MAX_AGE_DAYS = 30` and not more than a day in the future;
   `ran > 0`, `failed == skipped == not_run == 0`, `ran == declared`.
   The first failing check ends the run with exit 3 and one line naming it.
4. `--print-channel` prints `final` or `prerelease` and `--notes` prints the
   Markdown section for the release notes (the evidence file name, version, date,
   counts, or the pre-release sentence).

30 days balances a release cadence of weeks against the risk that the Apple
runtime shipped a new patch release; `SUPPORTED_CONTAINER_VERSION` already pins
the runtime, so age mainly guards against forgotten reruns. It is a constant plus
`--max-age-days`, documented in `docs/releasing.md`.

### D9. Workflow wiring

- `ci.yml`, `conformance` job: the report step becomes
  `... python3 scripts/test_report.py --require conformance`. The following
  report-recording step keeps `if: always()`, so a failing job still uploads its
  report.
- `release.yml`, `checks` job: a step "Release evidence (final tags)" runs
  `python3 scripts/verify_release_evidence.py --tag "$TAG"` with
  `TAG: ${{ github.ref_name }}` in `env`, never interpolated into the shell line.
  The checkout needs no history because the trees come from `HEAD`.
- `release.yml`, `release` job: a step computes the channel with
  `--print-channel`, another appends `--notes` to `RELEASE_NOTES.md`, and
  `softprops/action-gh-release` gets `prerelease: ${{ steps.channel.outputs.prerelease }}`.
  The job's permissions and the pinned action SHAs do not change.
- `tests/test_workflows.py` gains assertions: the evidence step lives in
  `checks`, uses the environment variable, no job uses `self-hosted`, `release`
  passes `prerelease`, and `ci.yml`'s `conformance` job calls `--require`.

### D10. Documentation

`docs/releasing.md` gains the maintainer sequence (clean tree, run
`validate-runtime.py --evidence` on macOS 26 with the supported release, commit the
file, push, tag) and the limits: the file is not signed and anyone with write
access can create one, so it records a maintainer's attestation tied to the code
tree, not an independent proof. `docs/testing.md` documents `--require`.
`README.md` and `docs/` replace the "Docker drops anonymous volumes; the shim
keeps them" language with the new behaviour.

## Risks / Trade-offs

- [Data in anonymous volumes no longer survives `down` + `up`] -> It is Docker's
  behaviour and now matches; the proposal marks it BREAKING and the release notes
  must say so. `down` without `-v` still leaves the volume until the next `up`,
  so nothing is lost at `down` time.
- [Residue removal deletes a volume the user cared about] -> Only labelled,
  deterministic-name volumes of this project and service are removed; unlabelled
  ones stop the command; there is no prefix or glob removal.
- [Evidence is self-attested and can be forged] -> Documented limit. The check
  still catches the honest failure modes: stale runs, wrong runtime, changed code,
  partial live runs.
- [Hash covers only three directories] -> Packaging (`pyproject.toml`) and
  workflow changes after the evidence do not invalidate it. Accepted: they do not
  alter the code the live tier exercises, and widening the set would make every
  README edit a re-validation.
- [`--require` is stricter than today for `live` when an image cannot be pulled]
  -> Intended for evidence; CI never requires `live`.
- [The live test for fixture B (PostgreSQL readiness race) already fails on this
  machine, and `--evidence` writes nothing unless every live test passes] -> The
  first real evidence file needs that flake fixed first, as a separate change;
  weakening the evidence rule to hide it would defeat its purpose.
- [A new maintainer step before every final tag] -> The cost is the point of
  option B; pre-release tags stay cheap for testing the pipeline.
- [Tag classification regex rejects `vX.Y.Z.postN`] -> Intentional: a post
  release is rare here, and failing loudly is safer than skipping evidence.

## Migration Plan

1. Merge the code. Existing projects with leftover anonymous volumes get fresh
   ones on the first `up` that creates a container (one-time loss of stale data).
2. Before the next final tag, run `scripts/validate-runtime.py --evidence`, commit
   the file, then tag. Until a first evidence file exists, only pre-release tags
   can be published; document this in `docs/releasing.md`.
3. Rollback: revert the commit; evidence files are inert without the verifier.
