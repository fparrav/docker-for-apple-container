## 1. Test support and scenario tests (design D5)

All of group 1 edits `tests/test_compose.py`, so 1.1 and 1.2 run one after the other. Tests that exercise the fix fail until 2.2 is applied; that is expected and each task says which.

- [x] 1.1 Read network creation from the runtime call log and fix the vacuous test @route:pi-qwen/xhigh
  - Priority: P0. Files: `tests/test_compose.py`. Depends: none.
  - Change: add `created_networks()` to the compose test base class, returning the last argv element of every `network create` call in `runtime_calls()`. In `test_external_network_is_attached_by_its_own_name_and_peers_resolve`, replace the `action_log` assertion with `self.assertEqual(self.created_networks(), [])` and add `assertNotIn("demo_default", state["networks"])`. Do not change `action_log` or the fake's behaviour.
  - Tests: a small test where `up` creates `demo_back` and `created_networks()` equals `["demo_back"]` and matches the keys of `state["networks"]`.
  - Done: the helper test passes; the repaired external-network test fails before 2.2 for the right reason (a `demo_default` creation) and passes after; `python3 -m unittest tests.test_compose -v` shows no other new failure.

- [x] 1.2 Add the network scenario tests @route:pi-qwen/xhigh
  - Priority: P0. Files: `tests/test_compose.py`. Depends: 1.1.
  - Change: add a test class `ImplicitDefaultNetworkTests` using the existing fake and `write_compose`. Every test asserts `created_networks()` and the final `state["networks"]`, including labels where a network is created. Cases, one test each: (1) service without `networks` creates `demo_default`; (2) one external `edge`, no `network create`; (3) one managed `back` creates only `demo_back`; (4) mixed services create `demo_default` and `demo_back` once; (5) `networks: [default]` and `networks: [default, back]`; (6) a declared top-level `default` that no service uses creates nothing; (7) missing external network exits 1 with no resource created, no `demo_default` included, and `assert_nothing_was_created()` passes; (8) used `demo_default` labelled for another project exits 1 naming the owner and creates nothing; (9) unused `demo_default` labelled for another project does not block `up`; (10) `--dry-run` prints only the used networks, in the order of the plan, and `assert_nothing_was_created()` passes; (11) `up api` leaves `demo_default` uncreated when only `web` uses it; (12) `up web` where `web` depends on `db` on `back` creates both; (13) `up --no-deps web` does not create `back`; (14) `down` for a project that never created `demo_default` exits 0 with no `network rm demo_default`; (15) `down` still removes a `demo_default` labelled for the project; (16) a characterization test of what the official normalizer yields for `networks: []` and `networks: {}` (design D2), asserting the existing behaviour.
  - Pin the dry-run output for the common single-network case (`network demo_default`) so existing dry-run expectations stay valid.
  - Done: cases 1, 3 (as to the single network), 5, 8, 14 and 15 pass before 2.2; cases 2, 3 (as to `demo_default`), 4, 6, 7, 9, 10, 11, 12 and 13 fail before 2.2 only for the reason stated and pass after it. State the before and after counts in the report.

- [x] 1.3 Add the dry-run scenario tests found by the independent review @route:pi-qwen/xhigh
  - Priority: P0. Files: `tests/test_compose.py`. Depends: 1.2 (done).
  - Change: in `ImplicitDefaultNetworkTests`, add `--dry-run` tests for the three scenarios of `specs/compose-dry-run/spec.md` that no test covers directly: (a) a project whose services use only an existing external `edge` and a managed `back`: exit 0, the output lists `edge (external, must exist)` and `demo_back`, does not list `demo_default`, and `assert_nothing_was_created()` passes; (b) a used external network that does not exist: exit 1, the same message as a real `up`, and no plan printed; (c) `demo_default` labelled for another project while no service uses `default`: exit 0 and no conflict printed. Assert `created_networks()` and the final `state["networks"]` in each.
  - Done: `python3 -m unittest tests.test_compose -v` passes in full; the three new tests fail if `_print_dry_run` or `_check_resources` is made to include `default` again (the coordinator checks this by reverting the source temporarily).

## 2. Source (design D1, D2, D3)

- [x] 2.1 Compute the networks of a plan in `compose_plan` @route:pi-qwen/xhigh
  - Priority: P0. Files: `src/container_docker_shim/compose_plan.py`, `tests/test_compose_plan.py`. Depends: none.
  - Change: add `service_network_keys(spec)` (the body of `compose._service_network_keys`, with the `["default"]` fallback unchanged) and a `networks: list[str]` field on `Plan` (default empty), filled by `plan()` with the distinct keys in first-use order over `ordered`. No runtime calls; keep the module pure.
  - Tests in `tests/test_compose_plan.py`: no networks gives `["default"]`; only `edge` gives `["edge"]`; a list and a mapping form; `default` named explicitly; duplicates across services collapse; order follows dependencies first; `no_deps` and a selection restrict the set; an empty mapping contributes nothing.
  - Done: `python3 -m unittest tests.test_compose_plan -v` passes on `python3` and `/usr/bin/python3`; `git diff` shows changes only in the two files.

- [x] 2.2 Use the plan's networks in `up`, the check and the dry run @route:claude-sonnet/medium
  - Priority: P0. Files: `src/container_docker_shim/compose.py`. Depends: 2.1, 1.2.
  - Change: `_service_network_keys` delegates to `compose_plan.service_network_keys`. `_check_resources` and `_print_dry_run` take the plan's network keys instead of adding `default`; delete the `setdefault("default", ...)` line and the `["default", ...]` prefix. In `cmd_up`, remove `_ensure_network(project, "default")` and the `referenced` seed, and iterate `service_plan.networks`. Keep the order, the `ShimError` texts and exit codes, `_ensure_network`, `down` and everything else as they are.
  - Applied as: `_check_resources` and `_print_dry_run` keep their signatures and read the keys of the plan's ordered services through the shared `compose_plan.service_network_keys`, so the set equals `plan.networks`; `cmd_up` iterates `service_plan.networks`. An existing test calls `_check_resources(proj, services)` with two arguments.
  - Done: the whole of `tests/test_compose.py` passes, including every case of 1.1 and 1.2; `python3 -m unittest discover -s tests` passes on `python3` and `/usr/bin/python3`; `git diff` shows no change outside `compose.py`.

## 3. Live fixture (design D6)

- [x] 3.1 Assert that no `default` network exists after the networks fixture runs @route:claude-sonnet/medium
  - Priority: P1. Files: `tests/test_live_fixtures.py`. Depends: none.
  - Change: in `test_fixture_e_services_resolve_only_peers_on_a_shared_network`, after the successful `up`, list networks with the runtime's `network ls` (as the neighbouring tests do for the nothing-created checks) and assert that `<project>_default` is absent while the fixture's own networks are present. Add no new test, so the declared live count stays at 40.
  - Tests: this is a live test and does not run on the worker's machine. Check it with `python3 scripts/test_report.py --tiers unit,fake`, confirm the live count of declared tests is unchanged, and read the diff against the harness helpers it uses.
  - Done: `python3 -m unittest tests.test_live_fixtures -v` imports and skips as before; the diff touches only that test.

## 4. Verification and consequences (design D7)

- [x] 4.1 Full local verification @worker:orchestrator
  - Priority: P0. Files: none. Depends: 1.2, 2.2, 3.1.
  - Done: `python3 -m unittest discover -s tests` and `/usr/bin/python3 -m unittest discover -s tests` (3.9.6) pass, run one at a time; `python3 scripts/test_report.py --tiers unit,fake` exits 0 and its generated `report.json` is removed afterwards; `python3 -m compileall -q src scripts tests` and `git diff --check` are clean; `python3 scripts/render_compat_table.py --check README.md` passes; `openspec validate fix-compose-default-network-provisioning --strict` passes; `git status` shows no change under `docs/evidence/`.

- [x] 4.2 Check the evidence impact without writing any evidence @worker:orchestrator
  - Priority: P0. Files: none. Depends: 4.1.
  - Done: in a throwaway worktree with a throwaway commit (deleted afterwards), `python3 scripts/verify_release_evidence.py --tag v9.9.9` exits non-zero naming the recorded hash and the current one, and `--tag v9.9.9rc1` exits 0 as a pre-release. Report both outcomes.

- [x] 4.3 Run the networks fixture live @needs-human
  - Priority: P1. Files: none. Depends: 4.1.
  - Why a person: it needs the maintainer's macOS 26 machine with `container 1.5.0` running and the fixture images.
  - Done: the live test of 3.1 passes, and by hand `docker compose up -d` on a project whose only service uses an external network leaves `container network ls` without `<project>_default`; `down` leaves the external network in place.

- [x] 4.4 Produce new live evidence before the first final tag @needs-human
  - Priority: P0 for a final release, not needed for pre-releases. Files: `docs/evidence/runtime-1.5.0-<timestamp>.json` (new). Depends: 4.3 and this change merged, together with any other change that edits `src/`, `scripts/` or `tests/`, so the working tree is clean.
  - Why a person: the run needs the maintainer's machine and the decision to commit the file.
  - Done: `git status --porcelain -- src scripts tests pyproject.toml` prints nothing, `python3 scripts/validate-runtime.py --evidence` exits 0 and writes one new file (40 declared, all passed), `python3 scripts/verify_release_evidence.py --tag v9.9.9` exits 0, and the file is committed on its own with a `docs/evidence` scope. Earlier evidence files stay unchanged.
