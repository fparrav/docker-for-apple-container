# Design

## Context

Observed state (read-only inspection):

- `.github/workflows/release.yml` is the only workflow. It publishes to PyPI
  through trusted publishing for `docker-for-apple-container` on release or
  manual dispatch, runs no tests, and uses floating action tags
  (`actions/checkout@v4`, `pypa/gh-action-pypi-publish@release/v1`).
- `pyproject.toml` names the distribution `docker-for-apple-container`, lists
  `AppCubic` as author, and points `Homepage` and `Repository` at
  `appautomaton/docker-for-apple-container`. The version is derived from Git tags
  by `hatch-vcs`. A comment asks for a refresh of an upstream-owned website.
- `origin` is `fparrav/docker-for-apple-container`; the repository has no
  workflow that runs `python3 -m unittest discover -s tests`.
- `cmd_version` prints `Client:\n Version: <container --version>\nServer:\n
  Engine: Apple container`. Hermes' contract test reads only the container line.
- Tests are stdlib `unittest` modules with a module-level `TIER`; tiers are
  enabled with `SHIM_TIERS=live` / `conformance`. `docs/testing.md` already
  describes a conformance CI recipe and a "CI limitation" (no hosted macOS 26
  runner with virtualization).
- `tests/test_docs.py` compares the README with `SUPPORTED_CONTAINER_VERSION`
  but does not look at other documents. The committed `AGENTS.md` still says
  1.4.1 (findings 10.1).

Constraints: Python 3.9 standard library only for test and report tooling;
workflows must not need secrets for pull requests; the shim must not gain a
runtime dependency.

## Goals / Non-Goals

**Goals:**
- Every pull request exercises the tiers that can run on hosted runners, and
  the result states plainly what did not run.
- A user can install this fork from a tag, see which shim and runtime
  versions are active, and does not depend on upstream infrastructure.
- Compatibility claims in the matrix are checked against evidence produced in
  the same run.

**Non-Goals:**
- A hosted macOS 26 virtualization runner (not available; the runtime tier
  stays manual on the owner's machine).
- Publishing to PyPI or a Homebrew tap now.
- Changing translations or Compose behaviour.
- Editing `AGENTS.md`; that file belongs to the owner.

## Decisions

### D1. Distribution name is owner-configurable, default `docker-for-apple-container-fork`

The fork cannot publish under the upstream name, and an installed tool that
shares the name hides which project is active. The default is a name that is
obviously the fork and that `pip` resolves independently. The name is read
from `pyproject.toml` by the version lookup (`importlib.metadata.version(name)`)
and by the release workflow, never hard-coded in a second place; a test asserts
the single source. The import package stays `container_docker_shim` and the
executable stays `docker`, so scripts and agent configuration do not change.
Alternative considered: keep the name and rely on installing from Git only.
Rejected because two different projects would answer to the same metadata name
in one environment.

### D2. Shim version in `docker version`

`cmd_version` prints one more top-level line after the existing block:

```
Client:
 Version: <container --version>
Server:
 Engine: Apple container
Shim: <version>
```

The version comes from `importlib.metadata.version(<distribution>)`; when the
metadata is missing (running from `src/` without installation) it prints
`0+source` so it cannot be mistaken for a release. `hatch-vcs` yields a
development version for untagged commits; whether it succeeds with no tag at all
is unverified and is spike 1.1. No existing line is removed, which keeps the
Hermes and OpenClaw contracts untouched.

### D3. Install channel is the Git tag; releases are GitHub Releases

The README documents, in this order:
`uv tool install git+https://github.com/fparrav/docker-for-apple-container@vX.Y.Z`,
the `pipx install` equivalent, and `uv tool install .` from a checkout. The
release workflow triggers on `v*` tags, builds sdist and wheel, runs the
hosted-runner checks first, and attaches the files to a GitHub Release whose
notes come from `scripts/test_report.py --release-notes`. No index publishing
step exists in the workflow; a short section in `docs/releasing.md` lists what
the owner would need to enable it (trusted publisher on the index, a protected
environment, a distinct name from D1) so that turning it on is a deliberate
edit, not a default.
Alternatives: keep PyPI as default (couples to upstream's project and a
publisher that belongs to someone else); a Homebrew tap (needs a second
repository and a formula bump per release, deferred).

### D4. Tier outcomes and the report script

`scripts/test_report.py` runs the suite with a `unittest` result subclass that
records, per test, one of five outcomes and its tier (module `TIER`):

| Outcome | Meaning | Source |
|---|---|---|
| passed | test ran and succeeded | success |
| failed | assertion error or exception | failure, error |
| skipped | test declined to run for a stated reason | `skipTest` / `skipIf` inside an enabled tier |
| not run | tier not enabled, or enabled but its precondition is missing | tier filter, runtime probe |
| unsupported | matrix entry UNSUPPORTED whose refusal test passed | joined from the matrix |

Only the tiers named in `SHIM_TIERS` (plus `unit`, `fake`) are started; modules
of other tiers are enumerated but not executed, so their tests are counted as
not run with the reason "tier not enabled". For an enabled tier whose
precondition fails (apiserver down, `SHIM_CONFORMANCE_DOCKER` unset) the module
already skips; the script reclassifies a skip whose reason matches the tier's
precondition message to not run, and keeps other skips as skipped. The exact
reason strings are collected in one constants module used by both the tests and
the script (task 4.1), so the match is not text guessing.

Output: a machine readable `report.json` and a Markdown summary
(`$GITHUB_STEP_SUMMARY` when set). Exit status is non-zero iff any test failed
or a matrix entry claims a level the run reached with a failing test; not run
does not fail the build (it is reported). Alternative considered: parse
`unittest -v` text. Rejected as fragile.

### D5. Evidence tracing

`tests/compat/matrix.json` entries already carry an evidence level and test
ids. The report maps each test id to its outcome and computes `reached` as the
highest level (unit < fake < live < conformant) among that entry's tests with
outcome passed. An entry is flagged when `reached` is lower than `claimed`. The
README matrix block is unchanged (it states claims); the report is where claims
meet the run. The conformance job records `docker version --format
'{{.Server.Version}}'` and `docker compose version --short` into the report.

### D6. Workflows and trust boundaries

| Workflow | Trigger | Runner | Secrets | Permissions |
|---|---|---|---|---|
| `ci.yml` job `checks` | pull_request, push to main | `ubuntu-latest` | none | `contents: read` |
| `ci.yml` job `conformance` | same | `ubuntu-latest` (Docker preinstalled) | none | `contents: read` |
| `runtime.yml` | `workflow_dispatch` only | `[self-hosted, macOS, runtime-26]` | none | `contents: read` |
| `release.yml` | tag `v*` | `ubuntu-latest` | none | `contents: read`; `contents: write` only in the release job |

Third-party actions are pinned to a full commit SHA with a version comment.
`pull_request_target` is never used. The runtime workflow additionally requires
a protected environment (`runtime`) so that a run needs maintainer approval,
and `if: github.repository == 'fparrav/docker-for-apple-container'` so a fork
of the fork cannot schedule it. Python-side checks use `python3 -m unittest`;
the byte-compile uses `python3 -m compileall -q src scripts tests`. A text-based
test (`tests/test_workflows.py`) enforces the rules because the standard library
has no YAML parser: it greps the workflow files for `permissions:`,
`pull_request_target`, `uses:` lines not pinned to 40 hex characters, and
publishing actions.

### D7. Documentation consistency

`tests/test_docs.py` gains `test_documents_do_not_state_other_release`: for each
of `README.md`, every `docs/*.md`, `AGENTS.md`, `CLAUDE.md` present in the
checkout, find literals matching `container(?: CLI)? \d+\.\d+\.\d+` or
`Apple container \d+\.\d+\.\d+` and fail when any differs from the supported
release. Documents that refer to the constant instead of a literal need no
change. When a document does not exist the test reports it as not checked in
its skip message rather than passing silently. The owner's local `AGENTS.md`
already references the constant and passes; the committed copy (1.4.1) would
fail, which is the intended pressure; the change does not edit that file.

### D8. Upstream coupling

Attribution stays (README "Fork of" paragraph and MIT notice). Removed from
install and release paths: PyPI project URL, upstream homepage/repository in
metadata, the website refresh sentence in `pyproject.toml`, upstream badges.
`AGENTS.md` instructs refreshing an upstream-owned site after a runtime
release; this is the owner's file, so the change records it in
`docs/releasing.md` ("not applicable to the fork") and lists it in the final
report as an owner action.

## Risks / Trade-offs

- **Self-hosted runner on a public repository** -> manual trigger, protected
  environment, repository guard, no PR path; documented in `docs/releasing.md`.
- **Hosted Docker version drift changes conformance** -> the job records the
  engine and Compose versions in the report; a mismatch is visible, not hidden.
- **Renaming the distribution breaks `pip install docker-for-apple-container`
  users** -> intentional (they were installing upstream); release notes and the
  README say how to switch. Tracked as breaking in the first release.
- **hatch-vcs with no tags** -> unverified (spike 1.1); the fallback is a
  `fallback_version` in `[tool.hatch.version]`.
- **Reclassifying skips as not run depends on reason constants** -> kept in one
  module and covered by unit tests of the script.

## Migration Plan

1. Land D2 and the tests (no behaviour change for consumers).
2. Land the report script, then `ci.yml` (first run on a branch to confirm
   green before relying on it).
3. Land identity/rename together with the release workflow and docs in one
   commit group, then tag a prerelease (`v0.x.y-rc1` equivalent) and check the
   GitHub Release and the `uv`/`pipx` install from the tag.
Rollback: revert the workflow and metadata commits; nothing is published
outside GitHub Releases, so no external state needs unwinding.

## Open Questions

- Final distribution name (D1 default can be replaced by the owner before
  task 2.1).
- Whether the owner wants the runtime workflow to run automatically on pushes
  to main later; out of scope here.
