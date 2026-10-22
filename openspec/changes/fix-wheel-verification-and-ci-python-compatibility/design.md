## Context

Observed on `bd19186` (run 39797891319), and re-checked in the files and locally:

- `scripts/verify_wheel.py`: `find_wheel(dist)` returns `wheels[0]`, a path built from the caller's `--dist` value. `verify()` passes it to `check_contents` (opened from the caller's working directory, works) and to `install(wheel, env_dir, work)`, which calls `pip install ... str(wheel)` with `cwd=work`, a fresh `tempfile.mkdtemp(prefix="verify-wheel-")`. A relative `dist/x.whl` therefore resolves against the temporary directory. Both workflows call it with the relative `--dist dist`. `tests/test_verify_wheel.py` only uses absolute `tempfile` directories, which is why 87 tests passed (also on Python 3.9.6) while CI failed.
- The log message for that failure is `pip could not install <name>: ...`, with only the file name, not the path that `pip` tried.
- `ci.yml` (`wheel`) and `release.yml` (`smoke`) both declare `runs-on: ubuntu-latest` with `python: ["3.9", "3.x"]`. `ubuntu-latest` moved to Ubuntu 26.04 on 2026-10-19. The `actions/python-versions` manifest (fetched 2026-10-22) lists CPython 3.9.25 for Linux 22.04 and 24.04, x64 and arm64, and no 26.04 build; 3.10.22 does list 26.04. The other jobs use `3.x`: `checks` and `conformance` of `ci.yml` passed on 26.04 in that same run, and the `checks`, `package` and `release` jobs of `release.yml` use the same setup (`release.yml` has not run on 26.04 yet), so they do not depend on 3.9.
- CPython 3.9 reached end of life in October 2025. `requires-python = ">=3.9"` is the declared floor, the code uses `from __future__ import annotations` and runs the full wheel, workflow and evidence tests on 3.9.6 (87 tests, OK), so there is no technical evidence for dropping it.
- `tests/test_workflows.py` reads the workflows as text. `matrix_pythons()` expects a single `python: [...]` list line, and `check_wheel_job` / `check_release_packaging` assert that list equals `[floor, "3.x"]`. That helper must change with the matrix shape.
- Evidence identity (`scripts/verify_release_evidence.py`, `EVIDENCE_INPUTS`): sha256 over the git tree ids of `src`, `scripts`, `tests` and the blob of `pyproject.toml` at `HEAD`. The one committed file, `docs/evidence/runtime-1.5.0-20261022T125604Z.json` (schema 2, 40 of 40 live tests passed, trees `src c01aa81`, `scripts 93288c3`, `tests a398a5e`, `pyproject.toml 23f543f`), is bound to that code.
- The release workflow already builds once (`package`), uploads `dist/`, runs `smoke` per Python on the downloaded files and makes `release` depend on `[checks, smoke]` with `dist` downloaded again and no build. `tests/test_workflows.py` pins most of this. It has two gaps: nothing asserts that the artifact name uploaded equals the name downloaded, or that nothing bypasses a failed `smoke` (`continue-on-error`, `if: always()`).

## Goals / Non-Goals

**Goals:**
- Make the wheel check independent of the working directory, with a minimal change in one function.
- Keep Python 3.9 covered by an effective test on a runner that is known to provide it, without exposure to `ubuntu-latest` changes.
- Keep the release pipeline's guarantee: one build, the same artifact tested and published, publication blocked by any failure.
- Make logs say which interpreter and image were verified.
- Keep the live evidence honest: a change under `scripts/` or `tests/` needs new live evidence.

**Non-Goals:**
- Changing the Python floor, the evidence hash or its inputs, or the live tier.
- Compose or runtime behavior, new actions, new runners, new dependencies.
- Smoke-testing the sdist (it is attached to the release but not installed by the check today; recorded as a risk).
- Pinning the other jobs (`checks`, `conformance`, `package`, `release`) to an image: they need no interpreter older than `3.x`, and the first two passed on 26.04.

## Decisions

### D1. Resolve the wheel once, in `find_wheel`

`find_wheel` returns `wheels[0].resolve()`, and its docstring says the result is absolute. `verify`, `check_contents` and `install` keep their signatures and receive that path unchanged. The failure message in `install` becomes `pip could not install <absolute path>: <tail>`; the contents check keeps `wheel.name` in its messages, since they are about members of the archive.

*Alternatives:*
- `Path(args.dist).resolve()` in `main` fixes the CLI but leaves `verify()` and `find_wheel()` wrong for any other caller (the tests call `verify(dist, ...)` directly), and splits the rule in two places.
- `os.path.abspath` or `absolute()` in `install`: same duplication, and a later check that opens the wheel again would need it too.
- Run `pip` with `cwd` set to the caller's directory: it breaks the guarantee that subprocesses run outside the checkout.
- Copy the wheel into the temporary directory: more I/O, a second file to name in messages, and it hides the original path in errors.

`resolve()` also follows a symlinked `dist/` to the real file, which is what `pip` needs. On a missing directory `is_dir()` is false before `resolve()` is reached, so the existing "no wheel found in <dist as given>" message is unchanged.

### D2. The verifier reports its environment

After the contents check, `verify` adds `ok environment: Python <platform.python_version()> on <platform.platform()>` to its output, before the install. It shows the interpreter that runs the verifier, which is the one `venv` copies into the environment under test, so the smoke log of each leg states the version and the platform. No new CLI option and no writes to `GITHUB_STEP_SUMMARY`.

*Alternatives:* a `--report FILE` option, uploading per-leg artifacts and appending them to the release notes (more artifacts, more actions, a new place for the notes to be wrong); a static sentence in the release notes (drifts from the workflow). Rejected for scope; the leg logs plus `docs/releasing.md` give a precise answer and a test pins the documentation to the workflows (D6). If the owner wants the matrix quoted in the release notes it can be added later as its own change.

### D3. Explicit matrix legs: floor on `ubuntu-24.04`, newest on `ubuntu-latest`

Both `ci.yml` (`wheel`) and `release.yml` (`smoke`) use:

```yaml
runs-on: ${{ matrix.os }}
strategy:
  fail-fast: false
  matrix:
    include:
      - python: "3.9"      # floor from pyproject.toml (requires-python)
        os: ubuntu-24.04   # pinned: the last image with CPython 3.9 in actions/python-versions
      - python: "3.x"      # newest release
        os: ubuntu-latest  # moves with GitHub; surfaces image changes early
```

The job name stays `Wheel (Python ${{ matrix.python }})` / `Wheel smoke test (Python ${{ matrix.python }})`. `checkout`, `setup-python`, `download-artifact`, build and verify steps are unchanged, and `--dist dist` stays relative, which keeps the exact CI invocation that failed as the regression check.

*Alternatives evaluated:*

| Option | Result |
| --- | --- |
| Pin only the floor leg to `ubuntu-24.04`, keep `3.x` on `ubuntu-latest` (chosen) | Python 3.9.25 is available today; the job does not move under us; the modern leg still shows `ubuntu-latest` migrations early; no new action or dependency. |
| Pin every job to `ubuntu-24.04` | Stable, but hides that `ubuntu-latest` moved and leaves the modern leg untested on the new image; the other jobs have no need for it. |
| `ubuntu-22.04` for 3.9 | Also has 3.9.25, but it is the older image, so its retirement from GitHub's hosted runners comes first and the pin would need replacement sooner. |
| Install 3.9 with `uv` or `python-build-standalone` on `ubuntu-latest` | Works on any image but adds a tool and a download to the trust boundary (a new SHA-pinned action or a network fetch of an interpreter). Over-engineered for one interpreter. |
| A `python:3.9` container job | Ties the check to a registry image tag, not pinned by commit; different libc and tool layout than hosted runners. |
| Raise the floor to 3.10 | No technical evidence (tests pass on 3.9.6); users on 3.9 would be dropped to fix a CI label. Only justified if the pinned image stops offering 3.9 (see D7). |
| `continue-on-error` or `allow-failure` for 3.9 | Defeats the guarantee that a failed smoke blocks publication. |

### D4. One matrix shape, two copies kept equal by a test

Actions has no includes shared between workflow files, so the two matrices are copies. `tests/test_workflows.py` gains `matrix_legs(job)`, which returns the list of `{python, os}` pairs from `include:` entries, replacing `matrix_pythons`. `check_wheel_job` and `check_release_packaging` assert, for each job:

- the legs' Python values are exactly the floor from `pyproject.toml` and `3.x`;
- the floor leg's `os` is a versioned label (does not end in `-latest`) and the `3.x` leg's is `ubuntu-latest`;
- `runs-on: ${{ matrix.os }}` and `python-version: ${{ matrix.python }}` are used;
- `matrix.os` and `matrix.python` do not appear in a `run:` line;
- the legs of `ci.yml` and `release.yml` are equal.

Synthetic-text tests flag each violation (missing floor leg, `ubuntu-latest` on the floor, a literal `runs-on: ubuntu-latest`, different legs between files), as the existing tests do for the other rules.

### D5. Release pipeline guards, not a redesign

The release workflow already meets the six properties of the request (build once, same artifact downloaded by `smoke` and `release`, clean install per Python, version and entry point check, publication behind every leg, no rebuild). The review adds only static guards to `check_release_packaging`:

- the `name:` of `upload-artifact` in `package` equals the `name:` of `download-artifact` in `smoke` and in `release`;
- neither `smoke` nor `release` declares `continue-on-error`, and `release` has no `if:` line;
- `smoke` has `needs: package`.

If a guard finds a gap in `release.yml` itself, the same task fixes the workflow; as read today none is expected. The sdist is attached to the release and not installed; closing that is out of scope and listed as a risk.

### D6. Documentation and its test

`docs/releasing.md`: the sentence "all on hosted `ubuntu-latest` runners" becomes accurate (`smoke` and the `wheel` job use the explicit legs); the section on the wheel gets a table of legs (3.9 on `ubuntu-24.04`, 3.x on `ubuntu-latest`), the reason for the pin, the maintenance procedure (D7), a sentence that the verifier prints the interpreter and platform, and a note that changes under `scripts/` or `tests/` invalidate the evidence (D8). `docs/testing.md` "The wheel check" adds that `--dist` can be relative or absolute and that the CI runs it as `--dist dist`. `tests/test_docs.py` asserts the documented floor leg (version and runner) equals the workflows' and that both documents mention the invalidation of evidence by `scripts/` and `tests/`.

### D7. Maintenance of the pin

When GitHub retires `ubuntu-24.04`, or `actions/python-versions` stops listing 3.9 for it, the 3.9 leg fails at `Set up Python` with the same error shape as today. The procedure documented in `docs/releasing.md` is: check the manifest (`curl -s https://raw.githubusercontent.com/actions/python-versions/main/versions-manifest.json`, filter `3.9.*` for `platform_version`), move the floor leg to the oldest image that still lists the interpreter, or, if none does, raise `requires-python` to the lowest supported minor in a dedicated change that updates `pyproject.toml`, the matrices, the docs and the specs, which in turn invalidates the live evidence (`pyproject.toml` is an input). The floor leg is never removed or made non-blocking silently; D4 tests fail if it is.

### D8. Evidence consequences

The hash covers `src/`, `scripts/`, `tests/` and `pyproject.toml`. This change edits `scripts/verify_wheel.py`, `tests/test_verify_wheel.py`, `tests/test_workflows.py` and `tests/test_docs.py`, so `scripts/verify_release_evidence.py --tag vX.Y.Z` will fail with the recorded and current hashes once the code lands. Edits to `.github/workflows/*`, `docs/*.md` and `openspec/` do not move the hash. Consequences:

- A final tag needs a new `docs/evidence/runtime-1.5.0-<timestamp>.json` from `python3 scripts/validate-runtime.py --evidence` on the clean, merged tree, on macOS 26 with `container 1.5.0`. This is the last step, after every code and test change (same order as D10 of the previous change), a `@needs-human` task.
- The existing evidence file is not edited or deleted. The verifier takes the newest file, so the new one supersedes it; the old one remains as history.
- Pre-releases do not read the evidence, so the pipeline can be exercised with an `rc` tag before the new evidence exists.
- Packaging checks stay a separate tier from live: they never write into `docs/evidence/` (spec: "Packaging checks are not live evidence").

*Alternatives:* exclude `scripts/verify_wheel.py` and its test from the hash (weakens the binding the gate promises, needs a schema 3 and still changes `scripts/` and `tests/` entries); move the verifier out of `scripts/` (it would still need its tests under `tests/`, and the workflow tests change anyway). Rejected: the cost is one live run, and the hash stays a plain tree id.

## Risks / Trade-offs

- [Pinned `ubuntu-24.04` reaches end of support or loses 3.9] -> D7: the leg fails visibly; the documented response is a dedicated change. A future calendar reminder is not added; the failure is the signal.
- [Python 3.9 is past upstream end of life] -> the floor is kept as declared. Raising it is a separate product decision, not a CI fix; this change adds the information to the docs and makes it cheap (one value in `pyproject.toml`, matrices, docs).
- [The 3.9 leg has not run on a hosted runner since the wheel check was added] -> the first CI run after implementation is the test; local evidence is 87 tests passing on 3.9.6 (macOS, system Python). If it fails for a code reason, the fix follows the rule from the previous change: raise `requires-python` rather than bend the code, in its own change.
- [Matrix copies drift] -> D4 test compares the legs of both files.
- [`resolve()` changes how a symlinked wheel is named in messages] -> messages carry the resolved path, which is the file `pip` actually opens; the "no wheel found" message still uses the directory as given.
- [A test in `tests/` changes, so the evidence goes stale again] -> documented in D8 and in the tasks; the live run is the final task, and no other task changes `scripts/` or `tests/` after it.
- [The sdist published with the release is not installed by the check] -> out of scope; a follow-up could build it in the smoke job.
- [The previous change is complete but unarchived, and its fork-distribution/release-evidence deltas are not in the main specs] -> this change uses ADDED requirements only, so either archive order works; archiving the previous change first is still recommended so the specs the docs cite exist.

## Task dependencies

```text
1.1 tests (red)  ->  1.2 fix  ->  1.3 messages/environment line
2.1 ci.yml + test helper  ->  2.2 release.yml  ->  2.3 release guards
1.3 + 2.2  ->  3.1 docs + doc tests
all of 1-3  ->  4.1 full verification  ->  4.2 CI run (human)  ->  4.3 new live evidence (human)
```

Groups 1 and 2 touch disjoint files (`scripts/verify_wheel.py` and `tests/test_verify_wheel.py` against the two workflows and `tests/test_workflows.py`), so they can run in parallel by two workers; inside each group tasks are chained because they share files. Group 3 starts only when both have finished.
