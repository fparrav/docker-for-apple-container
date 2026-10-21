# Testing

The suite separates tests by **what they prove**. A passing run always says
which of these statements it covers:

| Tier | Proves | Needs | Runs by default |
| --- | --- | --- | --- |
| `unit` | A pure function behaves as specified (parsers, option rules, hashing) | nothing | yes |
| `fake` | The shim translates a Docker command into the right Apple `container` calls, against a fake `container` binary | nothing | yes |
| `live` | The command works on a real Apple `container` runtime | macOS 26 on Apple silicon, `container` 1.5.0 running | no, opt-in |
| `conformance` | The same fixture behaves the same on a real Docker Engine | a real Docker Engine that answers `info` (no Apple runtime) | no, opt-in |

A `fake` pass is **syntactic** compatibility only. A `live` pass is **local
execution** on Apple `container`. Neither says anything about Docker Engine;
only `conformance` does. Every test module declares its tier with a
module-level `TIER = "unit" | "fake" | "live" | "conformance"` line.

## Running

```bash
# unit + fake tiers (the default; starts no container)
python3 -m unittest discover -s tests -v

# add the live tier (needs a running apiserver at the supported version)
SHIM_TIERS=live CONTAINER_DOCKER_SHIM_CONTAINER=/opt/homebrew/bin/container \
  python3 -m unittest discover -s tests -v

# add the conformance tier (a real Docker Engine client, not this shim)
SHIM_TIERS=conformance SHIM_CONFORMANCE_DOCKER=/usr/local/bin/docker \
  python3 -m unittest discover -s tests -v
```

`SHIM_TIERS` is a comma-separated list (`live,conformance`). When a tier is
enabled but cannot run — `live` when the apiserver is down or the release
differs from the supported one, `conformance` when no Engine answers `info` —
plain `unittest` shows the gate's
`SkipTest`; `scripts/test_report.py` classifies that tier-level precondition
as **not run**, never as passed. A per-test skip inside a running tier stays
**skipped** (see [Test report](#test-report)). A report that did not run a
tier must say "live tier: not run".

## Test report

`scripts/test_report.py` runs the tiered suite and produces a five-outcome
compatibility report: a machine-readable `report.json` plus a Markdown
summary. It is how a run states which claims it actually proved.

### The five outcomes

Each test is recorded with its tier and one of four test-level outcomes; the
matrix adds a fifth, entry-level one:

| Outcome | Meaning |
| --- | --- |
| `passed` | the test ran and succeeded |
| `failed` | the test raised an assertion error or exception |
| `skipped` | the test declined to run for a stated, test-level reason |
| `not run` | the tier was not enabled, or it was enabled but its gate precondition failed (the test was never started) |
| `unsupported` | a matrix entry whose status is `UNSUPPORTED` and whose refusal test passed (entry-level, joined from the matrix) |

A skip whose reason matches a tier-gate precondition — the shared constants
in `tests/_outcomes.py` that the gates in `tests/tiers.py` raise — is
reclassified as **not run**; any other skip, a per-test precondition such as
a pinned image that is not already local, stays **skipped**. The match is
against the shared constants, not text guessing, so the two cannot drift
apart.

### Tier filtering

`unit` and `fake` always run; `live` and `conformance` run only when
requested, through `--tiers` or, when `--tiers` is absent, `$SHIM_TIERS`.
Tests of a tier that is not enabled are enumerated but never started and are
recorded as `not run` with the reason `tier not enabled`; an enabled tier
whose every test was reclassified as not run is itself reported as not run.

### Matrix evidence and flags

The report joins its results with `tests/compat/matrix.json`. Each entry
records `claimed` (the evidence level the entry claims), `reached` (the
strongest level — unit < fake < live < conformance — actually produced by one
of its passing tests in this run), one of the five outcomes, and `flagged`:
true when `reached` is lower than `claimed`. A flagged entry is reported, not
failed — it is the visible record that a claim was not reached in this run
(for example, a `live` claim on a hosted runner where only `fake` ran). The
README matrix block states the claims; the report is where claims meet the
run.

### Outputs and exit status

- `report.json` (path set with `--report`): per-test records (id, tier,
  outcome, reason), per-tier counts and status, and the joined matrix records
  with their outcome counts and flagged ids.
- A Markdown summary with the tier outcome table and the matrix evidence
  counts, appended to `$GITHUB_STEP_SUMMARY` when set, otherwise printed.
- `--release-notes` prints release-note text instead of the summary: the
  supported release, one line per tier (`<tier> tier: ran (…)` or `<tier>
  tier: not run`), and the compatibility claims not reached in the run.

The exit status is non-zero iff any test **failed**; `not run` and flagged
claims are reported, never failing.

### Running the report

```bash
# default: unit + fake (plus $SHIM_TIERS when set)
python3 scripts/test_report.py

# opt-in tiers
SHIM_TIERS=live python3 scripts/test_report.py
SHIM_TIERS=live,conformance SHIM_CONFORMANCE_DOCKER=/usr/local/bin/docker \
  python3 scripts/test_report.py

# exactly the named tiers, regardless of $SHIM_TIERS
python3 scripts/test_report.py --tiers unit,fake

# release-note text instead of the Markdown summary
python3 scripts/test_report.py --release-notes
```

## Safety rules for live tests

- Every object is named `shimtest-<topic>-<8 hex>` and removed by its exact
  name in `addCleanup`, including when the test fails.
- Cleanup lists objects by that prefix (or by a label) and never calls any
  prune. Objects without the prefix are never touched.
- Images are pinned (`alpine:3.20`, `nginx:1.27-alpine`, `postgres:17`,
  `redis:7-alpine`). The live tier states when an image has to be pulled; the
  first run therefore needs network access and some disk.

## Fixtures

Reusable, plain Compose files and Dockerfiles live in
`tests/fixtures/compat/<letter>-<name>/`. Their first lines state the tiers and
images they need:

```yaml
# tiers: live, conformance
# images: nginx:1.27-alpine
```

The live and conformance tiers use the **identical, unmodified** file, which is
the point of the conformance tier.

A fixture can describe something the shim must refuse. `n-network-none` asks for
`network_mode: none`: the live tier asserts `up` exits 64 with a `REFUSED:` line
and creates nothing, while the conformance tier asserts real Docker starts the
service with only the loopback interface. `m-readonly-volume` mounts a named
volume and a bind read-only in the short (`:ro`) and the long (`read_only: true`)
syntax, plus a read-write control, and both tiers assert that the four writes
fail and the control succeeds. `r-selinux-refused` asks for `:Z`: the live tier
asserts `up` exits 64 with a `REFUSED:` line naming SELinux and creates nothing,
while the conformance tier asserts real Docker accepts the file (the documented
difference).

Two fixtures cover resource identity and service selection.
`o-external-volume` declares a volume `external: true` with `name: ${EXT_VOLUME}`;
the test pre-creates a disposable `shimtest-` volume, passes its name through a
`.env` override, and asserts the data survives `down -v` and a second `up` and
that no `<project>_` copy is created. `p-selective-up` has `api` depending on
`db` (healthy) plus an unrelated `worker`: `up -d api` must create `db` and
`api` only, after `db` is healthy, and `up -d --no-deps api` creates `api` alone.

`q-build-secrets` builds an image whose Dockerfile reads a file secret and an
environment secret renamed through `target` with `RUN --mount=type=secret`. The
Dockerfile carries only the SHA-256 of each dummy value, because the build log
echoes every `RUN` line. The environment secret reaches the build only through
the process environment (`Q_ENV_SECRET`), the image name arrives through a
`.env` override, and the test removes the image by its exact name. The live
tier also asserts that `up` exits 64 naming the variable, and creates nothing,
when `Q_ENV_SECRET` is not exported.

## Conformance tier in CI

The conformance tier needs a Docker Engine, so it is meant for CI or on-demand
runs, never for normal development. Its gate requires only a reachable Docker
Engine — it never probes the Apple `container` runtime — so it runs on any
Docker-only host. On a runner that has Docker:

```yaml
- run: SHIM_TIERS=conformance SHIM_CONFORMANCE_DOCKER=$(which docker) python3 -m unittest discover -s tests -v
```

`SHIM_CONFORMANCE_DOCKER` must point at a real Docker client, because on a Mac
with this project installed `docker` is the shim itself. Hosted macOS runners
have no virtualization, so the `live` tier cannot run there; on them only
`unit` and `fake` do. The `conformance` tier is not bound by that: it runs on
hosted Linux runners, which ship a Docker Engine, without any Apple runtime.

## Advancing the supported runtime

The shim supports exactly one Apple `container` release,
`SUPPORTED_CONTAINER_VERSION` in `src/container_docker_shim/cli.py`. Moving to
a newer release is a deliberate change, never an automatic one:

1. Install the candidate release on a macOS 26 machine with a running apiserver
   (`container system start`).
2. Run the validator. It never edits the supported version:

   ```sh
   scripts/validate-runtime.py --container /path/to/new/container
   ```

   The read-only part asks the candidate's own `--help` whether every Apple
   command and flag named in `tests/compat/matrix.json` still exists. The live
   part runs `SHIM_TIERS=live` against the candidate (disposable `shimtest-`
   objects only) and maps each result back to the matrix entries that cite it.
   Use `--skip-live` for the read-only check alone. The output is a per-entry
   evidence table; a tier that did not run is reported as "not run", never as
   passed.
3. Fix every entry the validator reports (`missing`, `FAILED`): update the
   translation, its tests and the matrix entry together.
4. Only when the validator reports no problems, change
   `SUPPORTED_CONTAINER_VERSION`, the test fixtures and the README together,
   then refresh the published website as described in `AGENTS.md`.

## CI limitation

The hosted `ubuntu-latest` jobs in CI cannot run Apple's `container` runtime,
so they do not run the `live` tier or `scripts/validate-runtime.py`. The CI
workflow runs `unit` and `fake` on Ubuntu; `conformance` also runs there using
the runner's real Docker Engine. The default `python3 -m unittest discover -s
tests -v` run starts only the `unit` and `fake` tiers.

The `conformance` tier is not bound by the missing virtualization: in
`.github/workflows/ci.yml` its job runs on `ubuntu-latest`, points
`SHIM_CONFORMANCE_DOCKER` at the runner's real Docker client, and its gate
probes that Engine with `docker info` — it never probes the Apple `container`
runtime. That is **container conformance**: it proves the same fixtures behave
the same on a real Docker Engine (and records the Docker and Compose versions
in the report), but it says nothing about the Apple `container` runtime.

The full Apple runtime checks — the `live` tier and
`scripts/validate-runtime.py` — therefore require the manual protected runner:
the `workflow_dispatch`-only runtime workflow on the self-hosted macOS 26
runner with the protected `runtime` environment (see [Running the runtime
workflow](releasing.md#running-the-runtime-workflow)). A release is not
advanced on hosted CI alone; the release process and the manual runtime
validation are documented in [Releasing](releasing.md).
