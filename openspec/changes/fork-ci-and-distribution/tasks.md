# Tasks

Conventions for every task:

- Python 3.9 standard library only, `from __future__ import annotations`, unittest only; tests are top-level modules in `tests/` with a module-level `TIER`; live tests are opt-in (`SHIM_TIERS=live`), use `shimtest-` names, clean up by exact name, never prune.
- Each task lists `Priority` (P0 must ship, P1 should ship, P2 may follow), `Files`, `Depends` and `Done`.
- Workflows are never run against the owner's machine or the Raspberry Pi from these tasks; the runtime workflow is only written, not triggered.
- Do not stage `AGENTS.md`. Do not publish anything to a package index.

## 1. Spikes (results in a new `findings.md` of this change)

- [x] 1.1 Build the wheel in a scratch clone with `python -m build` (a) at an untagged commit with no tags, (b) after a local tag `v0.0.1-spike`; record the resulting versions and whether the build fails without tags; delete the clone @worker:orchestrator @review:none
  - Priority: P0. Files: `findings.md`. Depends: none.
  - Done: findings.md states both versions and whether `fallback_version` is required.
- [x] 1.2 From a scratch clone tagged locally, run `uv tool install git+file://<clone>@<tag>` and `pipx install` into a temporary `UV_TOOL_DIR`/`PIPX_HOME`; record the install commands that work, where `docker` lands and what `docker version` prints with the runtime stopped @worker:orchestrator @review:none
  - Priority: P1. Files: `findings.md`. Depends: none.
  - Done: findings.md lists the verified commands; the temporary directories are removed; the user's tool environment is untouched.
- [x] 1.3 Record the full commit SHAs and release tags of the actions to be used (`actions/checkout`, `actions/setup-python`, `actions/upload-artifact`, `softprops/action-gh-release` or `gh release create`) from their repositories @worker:orchestrator @review:none
  - Priority: P0. Files: `findings.md`. Depends: none.
  - Done: findings.md has one line per action with SHA and version.

## 2. Identity and version

- [x] 2.1 Rename the distribution per D1, replace authors and project URLs with the fork's, drop the upstream website sentence, add `fallback_version` if 1.1 requires it; keep license and `docker` entry point
  - Priority: P0. Files: `pyproject.toml`, `tests/test_packaging.py` (new, `TIER = "unit"`). Depends: 1.1.
  - Tests: the name differs from `docker-for-apple-container`; URLs contain `fparrav/docker-for-apple-container`; the entry point is `docker = container_docker_shim.cli:main`; no `appautomaton` in the file.
  - Done: `python3 -m unittest tests.test_packaging -v` passes and `python -m build` still produces both files.
- [x] 2.2 Add `shim_version()` (metadata lookup with the `0+source` fallback, distribution name read from `pyproject.toml` when running from a checkout) and print the `Shim:` line in `cmd_version`
  - Priority: P0. Files: `src/container_docker_shim/__init__.py` or `cli.py`, `tests/test_cli.py`, `tests/test_hermes_contract.py`. Depends: 2.1.
  - Tests: version output keeps `Client:`, ` Version:`, `Server:`, ` Engine:` lines and adds `Shim: <v>`; fallback value when the metadata lookup raises; the Hermes parser still extracts the container version.
  - Done: `python3 -m unittest tests.test_cli tests.test_hermes_contract tests.test_openclaw_contract -v` passes.
- [x] 2.3 Add matrix entry for `docker version` Shim line (kind `command`, evidence `unit`) and regenerate the README block
  - Priority: P1. Files: `tests/compat/matrix.json`, `README.md`. Depends: 2.2.
  - Done: `python3 scripts/render_compat_table.py --check README.md` exits 0.

## 3. Documentation

- [x] 3.1 Rewrite the README install section and header: fork notice linking upstream, installation from tag with `uv` and `pipx`, from source, `command -v -a docker` to reveal order, absolute-path and `PATH` guidance for agents with minimal environments, `docker version` verification, how users of upstream's package switch; remove upstream badges, tap and site links
  - Priority: P0. Files: `README.md`, `tests/test_docs.py`. Depends: 2.1, 1.2.
  - Tests: the README contains no `appautomaton` install source or badge; it contains the fork repository URL in the install commands; it states the supported release once.
  - Done: `python3 -m unittest tests.test_docs -v` passes.
- [x] 3.2 Extend `tests/test_docs.py` per D7: scan README, `docs/*.md`, and `AGENTS.md`/`CLAUDE.md` when present for release literals that differ from `SUPPORTED_CONTAINER_VERSION`; unit tests of the scanner with in-memory text (stale literal fails, constant reference passes, absent file reported not checked)
  - Priority: P0. Files: `tests/test_docs.py`. Depends: none.
  - Done: the tests pass with the owner's local `AGENTS.md`; pointing the scanner at the HEAD copy of `AGENTS.md` (`git show HEAD:AGENTS.md`) fails naming 1.4.1 (check once, do not commit that file).
- [x] 3.3 Add `docs/releasing.md`: tagging, what the release workflow does, release notes content, how to run the runtime workflow, runner setup and trust rules, conditions to enable index publishing, the owner action about the upstream website sentence in `AGENTS.md`
  - Priority: P1. Files: `docs/releasing.md` (new), `docs/testing.md`. Depends: 5.1.
  - Done: `docs/testing.md` links to it and its "CI limitation" section matches the workflows; `python3 -m unittest tests.test_docs -v` passes.

## 4. Test report

- [x] 4.1 Add `tests/_outcomes.py` with the precondition reason constants used by the tier gates, and update their skip sites to use them
  - Priority: P0. Files: `tests/_outcomes.py` (new), `tests/tiers.py`, `tests/test_live_fixtures.py`, and the conformance fixture module present in `tests/`. Depends: none.
  - Done: the default run still skips/passes exactly as before (`python3 -m unittest discover -s tests` counts unchanged except for message text).
- [x] 4.2 Add `scripts/test_report.py`: tier filter, result collector with the five outcomes of D4, matrix join of D5, `report.json`, Markdown summary, `--release-notes`, exit status rule
  - Priority: P0. Files: `scripts/test_report.py` (new), `tests/test_test_report.py` (new, `TIER = "unit"`). Depends: 4.1.
  - Tests (with synthetic result sets, no subprocess): tier not enabled -> not run; enabled tier with precondition skip -> not run; ordinary skip stays skipped; UNSUPPORTED with passing refusal -> unsupported; a failure yields exit 1; claimed `live` with only fake passing is flagged; release notes contain "live tier: not run" when live did not run.
  - Done: `python3 -m unittest tests.test_test_report -v` passes and `python3 scripts/test_report.py --tiers unit,fake` produces a report with live and conformance as not run.
- [x] 4.3 Document the report and the five outcomes in `docs/testing.md`
  - Priority: P1. Files: `docs/testing.md`. Depends: 4.2.
  - Done: `python3 -m unittest tests.test_docs -v` passes.
- [x] 4.4 Make the conformance gate independent of Apple `container`: require the conformance tier and Docker client, probe the real Engine with `docker info`, and report an unavailable Engine as a tier precondition (`not run`); update the gate tests and conformance docs
  - Priority: P0. Files: `tests/tiers.py`, `tests/test_tiers.py`, `tests/test_conformance_fixtures.py`, `docs/testing.md`. Depends: 4.1, 4.2, 4.3.
  - Done: focused tier/report/docs tests pass; a Docker `info` failure is `not run`, and conformance can run on a Docker-only host without an Apple runtime.

## 5. Workflows

- [x] 5.1 Add `.github/workflows/ci.yml` with jobs `checks` (unit and fake via `scripts/test_report.py`, matrix check, README block check, `compileall`) and `conformance` (same fixtures with `SHIM_TIERS=conformance` and `SHIM_CONFORMANCE_DOCKER` set; prints Docker and Compose versions; uploads `report.json`)
  - Priority: P0. Files: `.github/workflows/ci.yml` (new). Depends: 1.3, 4.2, 4.4.
  - Done: the file passes `tests/test_workflows.py` (5.4) and a trial run on a branch of the fork is green; the conformance job's result is recorded in the PR description or `findings.md`.
- [x] 5.2 Add `.github/workflows/runtime.yml` (manual only, self-hosted label `runtime-26`, protected environment `runtime`, repository guard, runs `scripts/validate-runtime.py` and the live tier, uploads the report); do not trigger it
  - Priority: P1. Files: `.github/workflows/runtime.yml` (new). Depends: 1.3, 4.2.
  - Done: `tests/test_workflows.py` passes; the file has no `pull_request` trigger.
- [x] 5.3 Replace `release.yml`: trigger on `v*` tags, run the checks job, build, create a GitHub Release with the distributions and generated notes, remove every index-publishing step and the trusted-publishing permission
  - Priority: P0. Files: `.github/workflows/release.yml`. Depends: 1.3, 2.1, 4.2.
  - Done: `tests/test_workflows.py` passes; the workflow text contains no `pypi`.
- [x] 5.4 Add `tests/test_workflows.py` (`TIER = "unit"`): every workflow declares top-level `permissions`; no `pull_request_target`; every `uses:` is pinned to 40 hex characters; only the release job grants `contents: write`; `ci.yml` has no self-hosted runner and `runtime.yml` has no `pull_request`; no `pypi` in any workflow
  - Priority: P0. Files: `tests/test_workflows.py` (new). Depends: none.
  - Done: the test passes on the three workflows and fails on a synthetic string with an unpinned action (check once in the test itself).

## 6. Closure

- [x] 6.1 Run `python3 -m unittest discover -s tests -v`, `python3 scripts/render_compat_table.py --check README.md`, `python3 -m compileall -q src scripts tests` and `openspec validate fork-ci-and-distribution --strict`; record results
  - Priority: P0. Files: none. Depends: 2.3, 3.1, 3.2, 3.3, 4.3, 4.4, 5.1, 5.2, 5.3, 5.4.
  - Done: all four commands exit 0.
- [ ] 6.2 Tag a prerelease on a branch of the fork, confirm the GitHub Release contents and notes, and the `uv`/`pipx` install from the tag; delete the prerelease and tag afterwards (ask the owner before pushing a tag)
  - Priority: P1. Files: none. Depends: 6.1. @needs-human
  - Done: the notes list the supported runtime and tier table; the install works; the prerelease is removed.
