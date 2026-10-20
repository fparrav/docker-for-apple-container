# Findings

Results of the spikes for this change. Each entry records the exact commands
run, the observed output, and the conclusion for the dependent tasks.

## Spike 1.1 — `python -m build` with and without tags

Setup:

- Scratch clone of the worktree in a temporary directory
  (`mktemp -d /tmp/dfa-spike-11-XXXXXX`), checked out on branch
  `fork-ci-and-distribution` at commit
  `15ad5f2d8f791d7325fb7333d85b4f43dbeac190`. The repository has no tags
  (`git tag -l` is empty), so the clone has none.
- Build command for every run: `uv run --with build python -m build`
  (plain `python3 -m build` is not available in the system Python; the
  isolated build environment resolves `hatchling==1.32.4`,
  `hatch-vcs==0.5.0`, `setuptools-scm==10.3.4`).
- The scratch clone and all of its build outputs were deleted after the
  spike.

### (a) Untagged commit, no tags

Command: `uv run --with build python -m build`

Observed (exit 0):

    * Building sdist...
    * Building wheel from sdist
    * Building wheel...
    Successfully built docker_for_apple_container-0.1.dev79+g15ad5f2d8.tar.gz and docker_for_apple_container-0.1.dev79+g15ad5f2d8-py3-none-any.whl

Resulting version: **`0.1.dev79+g15ad5f2d8`** (confirmed in the wheel
`METADATA` and the sdist `PKG-INFO`: `Name: docker-for-apple-container`,
`Version: 0.1.dev79+g15ad5f2d8`). With no tag, setuptools-scm counts all 79
commits as distance (`git rev-list --count HEAD` = 79) and uses its built-in
`0.1` fallback base.

The build does **not** fail without tags.

### (b) Local tag `v0.0.1-spike`

Commands: `git tag v0.0.1-spike` (points at `15ad5f2`), then
`uv run --with build python -m build`.

Observed (exit 1):

    ValueError: Error getting the version from source `vcs`: Can't parse version from tag 'v0.0.1-spike' (tag_regex='^(?:[\\w-]+-)?(?P<version>[vV]?\\d+(?:\\.\\d+){0,2}[^\\+]*)(?:\\+.*)?$', tag_prefix='')

    ERROR Backend subprocess exited when trying to invoke build_sdist

No version was produced. Root cause (verified with
`setuptools_scm.get_version()` and `packaging.version.Version`): the default
tag regex captures the leading `v` into the version, and `packaging` rejects
`v0.0.1-spike` because `-spike` is not a valid PEP 440 pre-release label
(only `a`, `b`, `c`, `rc`, `alpha`, `beta`, `pre`, `preview`, `dev` are).
The leading `v` alone is tolerated.

### (c) Supplementary: valid tag `v0.0.1`

Commands: `git tag -d v0.0.1-spike && git tag v0.0.1`, then the same build.

Observed (exit 0):

    Successfully built docker_for_apple_container-0.0.1.tar.gz and docker_for_apple_container-0.0.1-py3-none-any.whl

Resulting version: **`0.0.1`** (exact; the tag points at HEAD, so distance is
0). A valid PEP 440 release tag works; only the non-PEP 440 spike label
breaks the build.

### Conclusion for task 2.1

- `fallback_version` is **not required**: the build succeeds with zero tags
  (hatch-vcs falls back to `0.1.dev<distance>+<node>` on its own), so an
  untagged build cannot fail for lack of a tag.
- Optional: set `fallback-version` in `[tool.hatch.version]` if the untagged
  version should be predictable — the `0.1` base is setuptools-scm's
  hardcoded default, not a project choice.
- Release tags must be valid PEP 440 versions: a `v*` tag such as
  `v0.0.1-spike` fails the build; use `v0.0.1`, `v0.0.1rc1`, or
  `v0.0.1-rc1` (relevant to the `v*` trigger in task 5.3 / D3).

## Spike 1.2 — `uv tool install` and `pipx install` from a local git tag

Setup:

- Scratch clone of the worktree in a temporary directory
  (`/tmp/dfa-spike12-d97ZcO/clone`), checked out on
  `fork-ci-and-distribution` at `15ad5f2`, plus the completed 2.1 changes
  (`pyproject.toml` renamed to `docker-for-apple-container-fork`,
  `tests/test_packaging.py`) committed locally in the clone as `98e58df` and
  tagged `v0.0.1` (a valid PEP 440 release tag; per spike 1.1 a non-PEP 440
  label such as `v0.0.1-spike` would fail the build). The tag was local to
  the scratch clone only; nothing was pushed.
- Machine: macOS 26 (26B118), Apple container CLI 1.5.0 (Homebrew),
  `uv 0.9.17`, `pipx 1.17.8` (Homebrew; its venvs use Python 3.14.8).
- The owner's `docker` (`~/.local/bin/docker`, a symlink to a source
  checkout) and the owner's `uv tool` / `pipx` environments were left
  untouched; every install went into a dedicated temporary directory.

### uv

Verified command (exit 0):

    UV_TOOL_DIR=/tmp/dfa-spike12-uv-M5PaWB \
    UV_TOOL_BIN_DIR=/tmp/dfa-spike12-uv-M5PaWB/bin \
    uv tool install git+file:///tmp/dfa-spike12-d97ZcO/clone@v0.0.1

Observed:

    Updating file:///tmp/dfa-spike12-d97ZcO/clone (v0.0.1)
        Updated file:///tmp/dfa-spike12-d97ZcO/clone (98e58dfd3ff37e271307824715e4d4cd015be60a)
    Resolved 1 package in 0.34ms
    Installed 1 package in 1ms
     + docker-for-apple-container-fork==0.0.1 (from git+file:///tmp/dfa-spike12-d97ZcO/clone@98e58dfd3ff37e271307824715e4d4cd015be60a)
    Installed 1 executable: docker
    warning: `/tmp/dfa-spike12-uv-M5PaWB/bin` is not on your PATH. ...

- Version resolved from the tag: `0.0.1` (dist-info
  `docker_for_apple_container_fork-0.0.1.dist-info`; venv Python 3.12).
- Where `docker` lands: `$UV_TOOL_DIR/bin/docker`, a symlink to
  `$UV_TOOL_DIR/docker-for-apple-container-fork/bin/docker` (the venv's
  console script; the venv directory is named after the distribution).
- `uv tool list` with `UV_TOOL_DIR` set shows
  `docker-for-apple-container-fork v0.0.1` / `- docker`.

Collision note (user-facing, relevant to task 3.1): the first attempt set
only `UV_TOOL_DIR`. uv built and installed the package, then refused to link
the executable: `error: Executable already exists: docker (use --force to
overwrite)` (exit 2). The executable goes to the tool *bin* directory
(default `~/.local/bin`), independent of `UV_TOOL_DIR`; a user who already
has any `docker` on PATH (upstream package, a source checkout, or Docker
Desktop) must uninstall it first or pass `--force`.

### pipx

Verified command (exit 0):

    PIPX_HOME=/tmp/dfa-spike12-pipx-YE4sBf/venvs \
    PIPX_BIN_DIR=/tmp/dfa-spike12-pipx-YE4sBf/bin \
    pipx install git+file:///tmp/dfa-spike12-d97ZcO/clone@v0.0.1

Observed:

    creating virtual environment...
    determining package name from 'git+file:///tmp/dfa-spike12-d97ZcO/clone@v0.0.1'...
    creating virtual environment...
    installing docker-for-apple-container-fork from spec 'git+file:///tmp/dfa-spike12-d97ZcO/clone@v0.0.1'...
    ⚠️  Note: docker was already on your PATH at /Users/felipe/.local/bin/docker
    ⚠️  Note: '/private/tmp/dfa-spike12-pipx-YE4sBf/bin' is not on your PATH ...
    done! ✨ 🌟 ✨
      installed package docker-for-apple-container-fork 0.0.1, installed using Python 3.14.8
      These apps are now available
        - docker

- Where `docker` lands: `$PIPX_BIN_DIR/docker`, a symlink to
  `$PIPX_HOME/venvs/docker-for-apple-container-fork/bin/docker` (pipx appends
  `venvs/` to `PIPX_HOME`; dist-info
  `docker_for_apple_container_fork-0.0.1.dist-info`, Python 3.14.8).
- pipx *warns* (does not fail) when `docker` is already on PATH — the new
  `docker` is exposed in pipx's bin dir, and PATH order decides which one
  wins. Relevant to the "switching from upstream" section of task 3.1.

### `docker version` with the container system stopped

Scope: `container system stop` stops the container system *services*
(apiserver, network, builder). It was issued before the observation, and the
subsequent `container system status` reported `apiserver is not running and
not registered with launchd` (exit 1) — valid evidence that the container
system service was stopped for this observation. The VM (machine) itself was
not checked and is not claimed to have been stopped; the runtime was running
(`container system status`: `status running`) before and after the spike.

`docker version` from the uv install (and identically from the pipx install),
captured with streams merged:

    apiserver is not running and not registered with launchd
    exit=1

The shim writes that line to stderr (it re-emits the `container system
status` output and returns its exit code) and prints nothing to stdout.

For reference, with the runtime running, `docker version` prints the normal
block (exit 0):

    Client:
     Version: container CLI version 1.5.0 (build: release, commit: unspeci)
    Server:
     Engine: Apple container

### Cleanup

The scratch clone, the temporary `UV_TOOL_DIR`, and the temporary
`PIPX_HOME`/`PIPX_BIN_DIR` were removed. The owner's tool environment
(`uv tool list`, `pipx list`, `~/.local/bin/docker`) is unchanged. The
runtime was left running; the internal `buildkit` builder container that
`container system stop` had stopped was restored to running by the
coordinator.

### Spike 1.3 — workflow action pins

Latest non-prerelease, non-draft releases were resolved through each official
repository's GitHub API `releases/latest` and `tags` endpoints, then cross-checked
with `git ls-remote https://github.com/{owner}/{repo} 'refs/tags/<tag>'
'refs/tags/<tag>^{}'`. The `^{}` ref is the commit SHA for annotated tags.

- `actions/checkout` — tag `v7.0.1`, commit `3d3c42e5aac5ba805825da76410c181273ba90b1`; [official repository](https://github.com/actions/checkout), verified by API and `git ls-remote` (lightweight tag).
- `actions/setup-python` — tag `v7.0.0`, commit `5fda3b95a4ea91299a34e894583c3862153e4b97`; [official repository](https://github.com/actions/setup-python), verified by API and `git ls-remote` (lightweight tag).
- `actions/upload-artifact` — tag `v7.0.2`, commit `cf430e030ddbb5b0abf93d22962f4752f3646cd9`; [official repository](https://github.com/actions/upload-artifact), verified by API and `git ls-remote` (lightweight tag).
- `softprops/action-gh-release` — tag `v3.0.3`, commit `efb35369e0ad2afab669f228072c1b0d510eae64`; [official repository](https://github.com/softprops/action-gh-release), verified by API and `git ls-remote` (`^{}` dereference; annotated tag object `e598afbe1493e6b1bafb1f389cabb956eab91231`).

### Closure validation — 2026-10-20

- `python3 -m unittest discover -s tests -v` — exit 0; 863 tests passed, 50 skipped.
- `python3 scripts/render_compat_table.py --check README.md` — exit 0.
- `python3 -m compileall -q src scripts tests` — exit 0.
- `openspec validate fork-ci-and-distribution --strict` — exit 0; change is valid.
- Targeted post-fix tests passed: `test_test_report.py` (19 tests), `test_workflows.py` (9 tests), and `test_image.py` (11 tests).
- CI branch trial: draft PR [#1](https://github.com/fparrav/docker-for-apple-container/pull/1), commit `06b089e`, Actions run [39411338351](https://github.com/fparrav/docker-for-apple-container/actions/runs/39411338351) — both `Hosted checks` and `Conformance (real Docker Engine)` passed. The conformance artifact reports 16 passed, 0 failed, 0 skipped. The first trial exposed a host-architecture-dependent image assertion; the test now derives the expected architecture from the runner.
- Task 5.1 is complete. The runtime workflow was not dispatched, and no release tag was created.
- Owner decision: defer task 6.2's tag/release/install trial. The current operational path is direct installation from the fork, and release distribution is not being used through CI yet. Before the first versioned release, protect the `v*` tag namespace and validate release contents, notes and `uv`/`pipx` installation. No tag or release was created for this change.
