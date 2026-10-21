# Audit findings (shared by four changes)

Date: 2026-10-19. Baseline: `main` at `b0b4666`, Apple `container` 1.5.0,
`docker-compose` at `/opt/homebrew/bin/docker-compose` (official normalizer).
The findings arrived as a static review of the public branch; every row below
was contrasted with the working tree. "Reproduced" means a command was run
against the translator without starting a container (read-only), "Read" means
confirmed by reading code and tests only, "Hypothesis" means it needs a live
check, which a task schedules.

Classification: **DEFECT** (confirmed bug), **RUNTIME LIMIT** (Apple 1.5.0 has
no equivalent), **BY DESIGN** (intentional, documented), **HYPOTHESIS**
(unverified), **SOLVED** (already handled).

Four changes own the work. `GATE` = `compose-capability-gate`,
`RES` = `compose-resource-identity`, `CLI` = `docker-cli-semantics`,
`FORK` = `fork-ci-and-distribution`.

## P0

| # | Finding | Class | Evidence | Owner |
|---|---|---|---|---|
| 2.1 | `network_mode: none` (and `host`, `service:x`, `container:x`) is not read anywhere; the service joins `<project>_default` | DEFECT, reproduced | `_build_run_args` for a service with `network_mode: none` yields `--network p_default`; `grep network_mode src/` is empty; the official normalizer does emit `"network_mode": "none"` | GATE |
| 2.2 | Any service key outside `SUPPORTED_SERVICE_KEYS` and `UNSUPPORTED_SERVICE_KEYS` is ignored without output (`pid`, `ipc`, `userns_mode`, `cpu_shares`, `mem_reservation`, `group_add`, `runtime`, `gpus`, `cgroup_parent`, `volumes_from`, `storage_opt` ...) | DEFECT, read | `_warn_unsupported` iterates only `UNSUPPORTED_SERVICE_KEYS`; `SUPPORTED_SERVICE_KEYS` is referenced only by tests. Spec `compose-normalization` "Unknown configuration is not interpreted silently" requires a warning and a refusal for restrictions, and no code implements it | GATE |
| 2.3 | Compose `build:` sub-keys other than `context`, `dockerfile`, `args`, `platforms` are dropped silently (`target`, `labels`, `no_cache`, `pull`, `secrets`, `ssh`, `cache_from`, `network`, `extra_hosts`, `ulimits`) | DEFECT, read | `_run_build` reads four keys. `build.target` ignored builds the wrong stage with exit 0. `cmd_build` in `cli.py` already supports `--target`, `--label`, `--no-cache`, `--pull`, `--secret`, `--ssh default` | GATE |
| 2.4 | Top-level `volumes`/`networks` attributes with runtime meaning are ignored (`driver`, `driver_opts`, `ipam`, `attachable`, `enable_ipv6`) | DEFECT, read | Only `external` (networks) and `internal` are read; a volume with `driver_opts` (for example NFS) silently becomes a local volume | GATE |
| 3.1 | `external: true` volume gets the project prefix: the shim creates and uses `<project>_shared_data` instead of `shared_data` | DEFECT, reproduced | `_named_volume_args` always calls `_volume_resource_name(project, source)`; `project.volumes` is never read. The official model already carries the resolved name (`shared_data`, `external: true`; managed volume `demo_data`) | RES |
| 3.2 | The existing unit test encodes the defect: it expects `demo_shared` to be created and removed by `down -v` while the real `shared` is "untouched" | DEFECT in test | `tests/test_compose.py` `_write_demo_compose` / `test_down_v_removes_only_project_labeled_volumes`: asserts `demo_shared` is a project volume | RES |
| 3.3 | `external: true` networks are looked up as `<project>_<net>`; when missing the shim only warns and `container run --network` then fails | DEFECT, read | `_ensure_network` computes `_network_resource_name` before consulting the spec | RES |
| 3.4 | Explicit `name:` on volumes and networks is not honoured (the official model resolves it) | DEFECT, read | resource names are always `project_key` | RES |
| 3.5 | `down -v` ownership is label based (`com.docker.compose.project`) and only removes labelled volumes | SOLVED, keep | `_load_labeled_resources`; spec `compose-volume-safety` "Down -v removes only owned volumes". Becomes sufficient once 3.1 stops creating a labelled copy of an external volume | RES |

## P1

| # | Finding | Class | Evidence | Owner |
|---|---|---|---|---|
| 4.1 | `up api` filters services by name before dependency closure, so `db` is not created, not waited for, and `service_healthy` is not evaluated | DEFECT, read | `cmd_up` filters `topo_sorted()` by `only`; `_selected_services` does the same for `pull`, `build`; no `--no-deps` | RES |
| 4.2 | `depends_on.required: false` is not parsed; a missing or unhealthy optional dependency is treated as required (healthcheck check refuses before creation) | DEFECT, read | `_parse_depends_conditions` reads only `condition` | RES |
| 4.3 | `topo_sorted` ignores a dependency on an undefined service | RUNTIME-INDEPENDENT NOTE | The official normalizer rejects it first; only the built-in parser reaches this path | RES (cover, no change) |
| 4.4 | `_enforce_run_security` and `_validate_dependencies` run on the selected subset only | BY DESIGN, becomes correct once 4.1 lands (the validated set equals the created set) | `cmd_up` | GATE/RES |
| 5.1 | `read_only: true` on a long-syntax named volume is dropped (`-v p_data:/data`, no mode) | DEFECT, reproduced | `_classify_and_translate_volume` passes mode `""` for non-bind entries | GATE |
| 5.2 | With the official normalizer the short form `shared:/s:ro` arrives as the long form with `read_only: true`, so the same drop hits `:ro` on named volumes in the default configuration | DEFECT, reproduced | `docker-compose config --format json` rewrites it to `{"type":"volume","source":...,"read_only":true}` | GATE |
| 5.3 | Whether Apple honours read-only on a named volume (`-v name:/t:ro` or `--mount type=volume,...,readonly`) is unknown | HYPOTHESIS | Apple help lists `--mount ...,readonly` and `-v`; no live write test exists | GATE (spike) |
| 5.4 | Other mount options (`bind.propagation`, `create_host_path`, `volume.nocopy`, `volume.subpath`, `tmpfs.size`, `type: tmpfs`, `type: npipe`, `:z`, `:Z`, `:cached`) are dropped or half handled | DEFECT, read | `_translate_volume` keeps only `ro` | GATE |
| 6.1 | `docker run -c` and `docker build -c` map to `--cpus`; in Docker `-c` is `--cpu-shares` | DEFECT, reproduced | `_parse_run_options(['-c','2','alpine'])` yields `--cpus 2`. Apple's own `-c` means `--cpus`, which is why the alias leaked in | CLI |
| 6.2 | The test suite encodes the alias (`RUN_SHORT_ALIASES["-c"] = "--cpus"`) | DEFECT in test | `tests/test_compat_matrix.py` | CLI |
| 6.3 | `docker run -v name:/path` (a Docker named volume) becomes `--mount type=bind,source=name,...` | DEFECT, reproduced | `_parse_run_options(['-v','data:/d','alpine'])`. Compose handles named volumes correctly; plain `run` does not. What Apple does with a relative `source` is unverified | CLI |
| 6.4 | `docker build --cpus` is an Apple-only flag; Docker `build` has no `--cpus`. `--runtime` and `--arch` on `run` are Apple semantics (Docker `--runtime` selects an OCI runtime such as `runc`) | HYPOTHESIS, low risk (fails loudly) | `RUN_VALUE_OPTIONS`; Apple `--runtime` = runtime handler | CLI |
| 6.5 | `build --rm` and `--force-rm` accepted as no-ops | BY DESIGN, acceptable: Apple builds do not leave intermediate containers behind. Docker's default is already `--rm=true`; `--rm=false` is the only misleading form | `BUILD_NOOP_OPTIONS` | CLI |
| 7.1 | `secrets` and `configs` (service level) warn and are ignored; the service starts without them | DEFECT, read | `UNSUPPORTED_SERVICE_KEYS`; reason text calls them "Swarm features", which is wrong for Compose | GATE |
| 7.2 | A faithful translation needs a single-file read-only mount at `/run/secrets/<name>` with mode and ownership | HYPOTHESIS | Apple `--mount` bind of a single file is unverified; mounting the parent directory would expose siblings | GATE (spike) |
| 8.1 | `docker compose ps --format json` is rejected with exit 64 although the README states it works | DEFECT, reproduced | `_parse_flags(sub_args, bools={"-q","-a",...})` raises "unsupported option: --format". `cmd_ps` prints a four-column tab table, not Docker's shape. The README sentence added on 2026-10-19 repeated the spec text without checking it | CLI |
| 8.2 | Other structured outputs (`docker ps/inspect/images --format`, `version`, `info`) | SOLVED | Contract tests `test_agent_contract.py`, `test_hermes_contract.py`, `test_openclaw_contract.py`, `test_inspect.py`, `test_container_presentation.py` | CLI (keep) |
| 8.3 | `docker version --format '{{.Server.Version}}'` (and `-f`) exits 64; plain `docker version` works | LIMITATION (loud, not misleading), reproduced | `cmd_version` rejects any argument; a common probe in agent tooling | CLI |
| 9.1 | Waivers (`CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED`) apply uniformly: a request for network isolation or a resource limit can be waived like a privilege request | DEFECT (policy), read | `security.decide` has one rule shape; skipping a restriction makes the container strictly more permissive than asked | GATE |
| 9.2 | Warning wording is not a stable token (`warning: --x is not supported ... NOT applied` for `run`, `service 'x': 'k' ignored` for Compose); both exit 0 | DEFECT (agent contract), read | `security.decide`, `_warn_unsupported` | GATE |
| 9.3 | `security.RULES` lists `hostname`, `add-host`, `network-none`, but the README says only six names are waivable and the CLI refuses them without consulting `decide` | INCONSISTENCY, read | `security.py`, README "Security options" | GATE |
| 10.1 | Committed `AGENTS.md` says Apple `container` 1.4.1; the working copy (uncommitted, excluded from the public repo on purpose) already refers to `SUPPORTED_CONTAINER_VERSION` | SOLVED locally; the committed file stays stale | `git show HEAD:AGENTS.md` vs working tree; the file must not be staged by this work | FORK |
| 10.2 | README lines added on 2026-10-19 claim `compose ps --format json` works (8.1) | DEFECT in docs | README "Behavior for scripts and agents" | CLI |
| 10.3 | README, `pyproject.toml`, `release.yml` still point to the upstream (`appautomaton` tap and site, PyPI project `docker-for-apple-container`, author `AppCubic`) | DEFECT in docs/metadata, read | README lines 4-8, 30-45; `pyproject.toml` urls; `release.yml` | FORK |
| 11.1 | No workflow runs the test suite on pull requests; `release.yml` publishes to PyPI on a release | DEFECT (process), read | `.github/workflows/` has only `release.yml` | FORK |
| 11.2 | The test report cannot say "unsupported" or "not run" distinctly from "skipped" | GAP | `unittest` skips; `scripts/validate-runtime.py` reports per matrix entry | FORK |

## P2

| # | Finding | Class | Owner |
|---|---|---|---|
| 12.1 | `docker version` prints only the container CLI version; the shim's own version is not shown, the clone has no git tags, so `hatch-vcs` can only derive a development version (not built here, unverified) | DEFECT, reproduced (`docker version` output) | FORK |
| 13.1 | `/etc/hosts` discovery, IP changes, distroless via `container cp`, host aliases | BY DESIGN, covered by `compose-service-discovery` and live fixture tests | none |
| 13.2 | Network aliases, multi-replica `scale`, `restart` policies, continuous healthchecks, `compose run` | BY DESIGN (README "intentionally out of scope"); only the *signalling* changes (see GATE: stable "NOT APPLIED" token) | GATE |
| 13.3 | External networks, multi-network services, dependency recreation, partial-failure recovery | covered by `compose-idempotent-up` and fixtures E and L; external network resolution is 3.3 | RES |
| 13.4 | Dependency images availability (pull of dependencies not selected today) | follows 4.1: closure also resolves images | RES |
| 13.5 | Exit-code propagation of `compose up` for a failed one-shot dependency and `compose exec` | SOLVED (`compose-dependency-conditions`, `agent-cli-contract`) | none |

## Not defects (discarded)

- "`down -v` can delete external volumes": it cannot, today, because the shim
  never touches the real external volume (see 3.1/3.2: the guarantee holds by
  accident, through the label). It becomes a defect only if 3.1 is fixed
  without an ownership check, which the RES change forbids.
- "Run security options accepted silently": `docker run` refuses unknown
  options (`unsupported docker run option`) and the six restriction options go
  through `security.decide`. The gap is Compose only (2.1, 2.2).
- "`--rm`/`--force-rm` misleading": see 6.5.
- "`topo_sorted` ignores undefined dependencies": official normalizer rejects
  first (4.3).

## Spike results

Run on 2026-10-19 against Apple `container` 1.5.0 with disposable `shimtest-` objects (removed afterwards; no other workload touched).

### S1: read-only named volumes (task 1.1)

| Form | Write inside the container | Exit |
|---|---|---|
| `-v <volume>:/t:ro` | `Read-only file system` | write rc 1 |
| `--mount type=volume,source=<volume>,target=/t,readonly` | `Read-only file system` | write rc 1 |
| `--mount type=volume,source=<volume>,target=/t` (control) | succeeds | write rc 0 |

Data written through the read-write mount was readable through the read-only mount, and `touch` on it failed. Both forms enforce read-only, so task 4.1 emits the `--mount type=volume,...,readonly` form (one form for binds and volumes) and does not refuse read-only named volumes.

### S2: single-file read-only bind mount (task 1.2)

`--mount type=bind,source=<file>,target=/run/secrets/x,readonly` fails before the container starts with `Error: path '<file>' is not a directory` (three attempts: root, uid 1000, mode 644). Apple `container` 1.5.0 does not support mounting a single file. Secrets alternative A (translate file secrets to `/run/secrets/<name>`) is therefore not available as a file mount; it would need a directory mount of a staged copy, which the shim must not create (no shim-owned state). Decision: alternative B (refuse) stands; no follow-up change for alternative A until the runtime supports file mounts.

## Integration results (task 6.2)

Run on 2026-10-19 against Apple `container` 1.5.0, `CONTAINER_DOCKER_SHIM_CONTAINER=/opt/homebrew/bin/container`.

| Check | Command | Result |
|---|---|---|
| Default suite | `python3 -m unittest discover -s tests` | 653 tests OK, 39 skipped (live and conformance tiers) |
| Runtime validator | `python3 scripts/validate-runtime.py` | 304 matrix entries, 0 problems |
| Live tier | `SHIM_TIERS=live python3 -m unittest -v tests.test_live_fixtures tests.test_live_security tests.test_live_compose` | 23 tests OK, including fixtures A to L, `m-readonly-volume` (writes to the volume and the bind fail) and `n-network-none` (`up` exits 64, nothing created) |
| Build target | Compose project with a three-stage Dockerfile and `build.target: runtime` | the container prints `runtime`, so the selected stage was built |
| Leftovers | `container ls --all`, `volume ls`, `network ls`, `image ls` filtered by `shimtest-` | none |
| Spec | `openspec validate compose-capability-gate --strict` | valid |

Not run: the conformance tier (`SHIM_TIERS=conformance`) needs a real Docker Engine and none was used in this run, so `m-readonly-volume` and `n-network-none` are unexecuted on real Docker. No failure was left unresolved.
