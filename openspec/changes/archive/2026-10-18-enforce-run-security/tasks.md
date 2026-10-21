# Tasks

Conventions for every task: Python 3.9 compatible (`from __future__ import annotations`), standard library only, English identifiers and comments, match the surrounding style. `security.py` MUST NOT import `cli` or `compose` (they import it). Opt-in option names are: `security-opt`, `pids-limit`, `storage-opt`, `privileged`, `sysctls`, `devices`.

## 1. Spikes against the live 1.5.0 runtime

- [x] 1.1 With one explicitly named disposable container, run `container run --cap-drop ALL`, `--read-only`, `--user 1000:1000` and `--tmpfs /tmp`, record in `findings.md` what each does inside the guest, remove the container, and verify `container ls -a` no longer lists it @worker:orchestrator @review:none
- [x] 1.2 Check whether `container run --cap-add NET_ADMIN` and `--cap-add CAP_NET_ADMIN` are both accepted and which one `container inspect` reports as `capAdd`, and record the answer in `findings.md` @worker:orchestrator @review:none
- [x] 1.3 Check whether `container run --tmpfs /tmp:ro,noexec,size=64m` fails, ignores the options or applies them, and record the answer in `findings.md` @worker:orchestrator @review:none

## 2. Classification table (`src/container_docker_shim/security.py`, new)

- [x] 2.1a Create `src/container_docker_shim/security.py` with a frozen dataclass `OptionRule(name, status, reason, apple_flag)`, status constants SUPPORTED/PARTIAL/UNSUPPORTED, a `RULES` dict keyed by option name containing every row of the design table (keys without leading dashes, e.g. `security-opt`, `cap-add`, `tmpfs`), and `lookup(name) -> OptionRule | None`; every UNSUPPORTED rule has a non-empty reason; verify with `python3 -c "from container_docker_shim import security"` from `src` @worker:free
- [x] 2.1b Create `tests/test_security.py` asserting every UNSUPPORTED rule has a reason, every SUPPORTED rule has an `apple_flag`, `lookup` returns None for an unknown name, and the opt-in names listed in the conventions all exist as UNSUPPORTED or PARTIAL rules; verify `python3 -m unittest tests.test_security -v` passes @worker:free
- [x] 2.2a Add to `security.py` `unenforced_allowed(environ=os.environ) -> frozenset[str]` reading `CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED` (comma-separated, whitespace trimmed, empty items dropped, no wildcard) and `decide(name, environ=os.environ) -> Decision` where `Decision.action` is `"ok"` (SUPPORTED or PARTIAL fully handled by the caller), `"skip"` (UNSUPPORTED and named in the opt-in; `message` is a warning) or `"refuse"` (UNSUPPORTED and not opted in; `message` names the option, the reason and the opt-in variable) @worker:free
- [x] 2.2b Extend `tests/test_security.py` with tests for `unenforced_allowed` (unset, one name, several names with spaces, an unknown name, a `*` value that grants nothing) and for `decide` (ok, skip, refuse, refuse when another option is the only one opted in); verify the file passes @worker:free

## 3. `docker run` and `docker create` (`cli.py`)

- [x] 3.1a In `src/container_docker_shim/cli.py` replace the silent no-op branch for `--security-opt`, `--pids-limit` and `--storage-opt` in the run-option parser with `security.decide(...)`: `refuse` raises `ShimError(message, 64)` before any runtime call, `skip` writes the warning to stderr and continues; add `--privileged` to the same path; leave the `cmd_create` `--storage-opt` probe (exit 125) as it is; verify `python3 -m unittest discover -s tests` shows only the expected failures in the Hermes contract @worker:local
- [x] 3.1b In `tests/test_cli.py` add tests that `run` with `--security-opt no-new-privileges`, `--pids-limit 100`, `--privileged` and `--storage-opt size=1G` each exit 64 and record no `run` or `create` call in `FAKE_CONTAINER_CALL_LOG`, that opting in to one option runs with a warning on stderr, and that opting in to one option still refuses another; verify the new tests pass @worker:local
- [x] 3.2 In `tests/test_cli.py` add tests that `--cap-add NET_ADMIN`, `--cap-add CAP_NET_ADMIN`, `--cap-drop ALL` and `--cap-drop NET_RAW` reach the runtime argv unchanged (finding 1.2: the runtime normalizes names and refuses invalid ones, so the shim adds no normalization), and that when the fake runtime fails a `run` with `invalid CapabilityName 'NOT_A_CAP'` the shim exits non-zero and passes the runtime message to stderr; verify they pass @worker:local
- [x] 3.3a Replace `_tmpfs_path` in `src/container_docker_shim/cli.py` so the `--tmpfs` value, options included, is forwarded verbatim to `container run` (finding 1.3: the runtime applies `ro`, `noexec`, `nosuid`, `nodev`, `size=` and `mode=`), and remove the debug line that discards options; verify `python3 -m unittest tests.test_cli` has no new failures @worker:local
- [x] 3.3b In `tests/test_cli.py` replace any test that expects tmpfs options to be discarded and add tests for `--tmpfs /tmp`, `--tmpfs /tmp:rw,noexec,nosuid,size=64m` and `--tmpfs=/run:size=1m`, asserting the exact `--tmpfs` argv the fake runtime receives; verify they pass @worker:local
- [x] 3.4 Update `tests/test_hermes_contract.py` so the existing contract run sets `CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED=security-opt,pids-limit` in `self.env` before calling `docker run`, keeps every other assertion, and add one test in the same class showing the same command is refused with exit 64 without that variable; verify `python3 -m unittest tests.test_hermes_contract -v` passes @worker:local

## 4. Compose (`compose.py`)

- [x] 4.1a In `src/container_docker_shim/compose.py` add a validation pass that runs in `cmd_up` for every selected service before the first `container run`, checking `privileged`, `security_opt`, `pids_limit`, `sysctls` and `devices` through `security.decide`, raising `ShimError(message, 64)` naming the service and key on `refuse` and warning on `skip`; remove `privileged`, `sysctls` and `devices` from `UNSUPPORTED_SERVICE_KEYS` @worker:local
- [x] 4.1b In `tests/test_compose.py` add tests that a service with `privileged: true`, with `security_opt` and with `pids_limit` makes `up` exit 64 with no recorded runtime `run` call, that a second valid service in the same file is not started either, and that the opt-in turns each into a warning; update any existing test that expected the old warn-only behaviour; verify `python3 -m unittest tests.test_compose -v` passes @worker:local
- [x] 4.2a In `compose.py` confirm each `tmpfs` entry in `_build_run_args` is forwarded verbatim (it already is) and add no code change unless a test in 4.2b shows otherwise @worker:orchestrator @review:none
- [x] 4.2b In `tests/test_compose.py` add tests for the generated `run` arguments of `ulimits`, `read_only: true`, `init: true`, `cap_add`, `cap_drop`, `user` and `tmpfs: ['/tmp:rw,size=64m']`; verify they pass @worker:local

## 5. Live verification (opt-in)

- [x] 5.1 Create `tests/test_live_security.py` with tests skipped unless `SHIM_TIERS` contains `live`, `container system status` reports the supported version, and the apiserver is running; each uses the name prefix `shimtest-sec-`, cleans up in `addCleanup` (remove by exact name only, no prune), and proves the effect of `--cap-drop ALL` (`CapEff` in `/proc/self/status` is all zeros), `--tmpfs /tmp:ro,noexec,size=64m` (the mount line shows those options), `--read-only` (a root write fails) and `--user 1000:1000` (`id -u` prints 1000); verify the module is skipped with a reason in a default run @worker:local
- [x] 5.2 Run the live tests with `SHIM_TIERS=live`, record real results in `findings.md` (a test not run is reported as not run), and verify `container ls -a` shows no `shimtest-sec-` container afterwards @worker:orchestrator @review:none

## 6. Documentation and validation

- [x] 6.1a Replace the README "Caveats" bullets about silent no-ops, the `--tmpfs` note and the Compose ignored-keys paragraph with the classification table (one row per rule in `security.py`, columns option, status, reason) and a short opt-in section naming `CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED` and its option names @worker:orchestrator @review:none
- [x] 6.1b Add to `tests/test_security.py` a test that every `RULES` entry appears in the README table with the same status; verify `python3 -m unittest discover -s tests -v` passes @worker:local
- [x] 6.2 Run `openspec validate enforce-run-security --strict` and verify it passes @worker:orchestrator @review:none
