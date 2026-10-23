## Context

`cmd_up` builds a plan (`compose_plan.plan`) that fixes which services an `up` acts on. Volumes already follow that plan: `_check_resources`, `_print_dry_run` and the creation loop all read `_service_volume_keys(service, project)` per planned service. Networks do not. Three places add `default` on their own:

| Place | What it does today |
| --- | --- |
| `cmd_up` | `_ensure_network(project, "default")`, then ensures every network the services name, with `default` always in `referenced` |
| `_check_resources` | `uses["network"].setdefault("default", ...)` after the per-service loop |
| `_print_dry_run` | iterates `["default", *keys]` |

`compose_resources.resolve` adds `default` to the resolved networks so that its runtime name (`<project>_default`, or the declared `name:`, or the bare key when external) is always known. That is a naming table, not a use: `down` reads it through `_removable_names`, which keeps only resources present in the runtime with this project's label.

The normalizer already fills `networks: {default: null}` for a service that declares none (`test_compose_normalizer.py`), so "implicit use" shows up in the normalized model as the key `default`. `_service_network_keys` keeps a `["default"]` fallback for specs that skipped normalization.

The fake runtime in `tests/test_compose.py` writes created networks to `state["networks"]` and records every argv it receives in `calls.log` (exposed by `runtime_calls()`), but only container actions go to `state["action_log"]`.

## Goals / Non-Goals

**Goals:**

- One definition of "the networks this `up` needs", shared by the check, the dry run and the creation.
- `default` is provisioned exactly when a selected service uses it.
- No side effect when a needed network is missing or conflicts.
- Tests that fail if the shim creates a network it does not need.

**Non-Goals:**

- Any change to naming, labels, `internal`, external handling or removal of networks.
- Changes to `down`, `network_mode` refusals, discovery, volumes, reconciliation or the capability gate.
- A new shim-owned state: Apple `container` stays the source of truth for what exists.

## Decisions

### D1: Extend the plan with the network keys

`compose_plan` gets a pure `service_network_keys(spec)` (the current `_service_network_keys` body, moved) and `Plan` gains `networks: list[str]`, the distinct keys in first-use order over `ordered` (dependencies first). `compose._service_network_keys(service)` delegates to it, so `_service_networks` and the attach code keep working.

`cmd_up` passes `service_plan.networks` to `_check_resources` and `_print_dry_run` and iterates it to create. Because the plan already feeds validation, check and creation, the sentence in its docstring ("the set that is checked is the set that is created") becomes true for networks too.

Alternatives considered:

- A helper in `compose.py` called three times: still three call sites that can drift, and not testable without a `Project`. Rejected.
- Creating networks lazily when a container is attached: the read-only check must see the full set before the first side effect, so the set has to exist before creation. Rejected.
- Dropping `default` from `compose_resources.resolve`: it would break the naming used by `down` and by a service that names `default`. Rejected.

### D2: `default` is provisioned only when used

A selected service uses `default` when its normalized `networks` contains the key `default` (implicit or named). The key is then handled like any other: a managed `default` is created if absent and checked for ownership; a declared external `default` must exist.

A declared top-level `default` that no selected service uses is not created and not checked. Rationale: it matches Docker Compose, and checking something `up` will not touch would fail a project over a resource it never attaches.

A service whose normalized `networks` is an empty mapping contributes no key (existing behaviour of `_service_network_keys`). Task 1.2 adds a test of what the official normalizer yields for `networks: []` and `networks: {}`; if it yields `default`, nothing else is needed, and if it yields an empty mapping the test documents the existing behaviour without changing it.

### D3: Selection and dependencies

The set follows `plan.ordered`, so `up api` counts only `api` and its required dependencies, and `--no-deps` counts only the named services. A dependency's networks are included because the dependency is started.

### D4: `down` stays as is

`down` removes only declared managed networks that exist with this project's label. A project that never created `default` produces no `network rm` for it, and one created by an earlier version is still removed. Tests pin both. A partial `down <service>` still removes no network.

### D5: Test infrastructure

`action_log` stays container-only. Many tests assert it equal to a full expected list or to `[]` after `clear_action_log()`, so adding entries to it would change dozens of unrelated assertions. The fake already records every argv in `calls.log`; the defect is that the vacuous test reads the wrong log. The change adds `created_networks()` (names from `network create` calls in `runtime_calls()`) and uses it, together with the final `state["networks"]`, in the network tests. `assert_nothing_was_created` already uses `runtime_calls()` and is the model.

### D6: Live fixture

`e-networks-volumes` (services `app`, `edge`, `store` on `front`/`back`, none on `default`) already runs live. One assertion is added to its existing network test: after `up`, `container network ls` does not list `<project>_default`. No live test is added, so the declared live count stays at 40.

### D7: Evidence consequence

The change edits `src/` and `tests/`, which are inputs of the tree hash in `docs/evidence/`. After merge, `scripts/verify_release_evidence.py --tag vX.Y.Z` fails for a final tag until `python3 scripts/validate-runtime.py --evidence` runs on the clean merged tree on macOS 26 with `container 1.5.0`. The existing file is never edited or renamed; the new file supersedes it. A pre-release tag needs none. Because the preceding change already moved the hash, one new evidence file after both changes are merged is enough.

## Risks / Trade-offs

- [Behaviour change] A project with only explicit networks no longer gets `<project>_default`. This is the intended fix and matches Compose; the proposal marks it breaking.
- [Order of dry-run output] `default` no longer prints first; it appears where the first service that uses it is. Mitigation: task 1.2 pins the order for the common single-network case so the existing dry-run expectations stay valid.
- [Transition] Networks created by earlier versions stay in place and are removed by `down`; nothing deletes them on `up`.
- [Fake fidelity] The fake's `network create` accepts any name. The new tests assert both the calls and the final state, so a wrong name is caught.

## Verification

- Pure tests for the plan (`tests/test_compose_plan.py`), run without Apple `container`.
- Fake-runtime tests for the ten scenarios in `tests/test_compose.py`, each asserting calls and final state.
- `python3 -m unittest discover -s tests -v` on 3.12 and on `/usr/bin/python3` (3.9.6), run one suite at a time because some tests compare the shared temporary directory.
- A read-only live check on macOS 26 is human-owned (tasks 4.3 and 4.4).

## Task dependencies

- 1.1 (test helper and the vacuous test), 2.1 (plan) and 3.1 (live fixture) touch different files and run in parallel.
- 1.2 (the scenario tests) edits the same file as 1.1 and follows it.
- 2.2 (wiring in `compose.py`) needs 2.1 and 1.2; the tests from 1.1 and 1.2 that fail before it turn green after it.
- 4.1 and 4.2 are coordinator checks after everything else; 4.3 and 4.4 need a person.
