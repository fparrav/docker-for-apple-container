# Design

## Context

- `_network_resource_name(project, net)` and `_volume_resource_name(project,
  vol)` return `f"{project}_{name}"` unconditionally. `_ensure_network` reads
  the network spec from a module-level dict `_NETWORK_SPECS` filled inside
  `cmd_up`; volumes have no spec lookup at all (`project.volumes` is never read).
- The official normalizer already resolves identity: managed resources carry
  `name: "<project>_<key>"`, external ones `name: "<key>"` and `external:
  true`, an explicit `name:` is copied through. The built-in parser exposes the
  raw top-level maps only.
- Ownership today is the label `com.docker.compose.project` read by
  `_load_labeled_resources`; `cmd_down` removes every labelled network, and with
  `-v` every labelled volume.
- `cmd_up` takes `project.topo_sorted()` and filters by name. `topo_sorted`
  already detects cycles and ignores undefined dependencies. `_inject_etc_hosts`
  runs on the whole project, so discovery is unaffected by a partial `up`.
- Apple `container` 1.5.0: `volume create|inspect|list|rm` with labels,
  `network create|inspect|list|rm` with `--label`, and `container list`
  rows carrying the labels the shim writes.

Motivation: `proposal.md`. Evidence: `compose-capability-gate/findings.md`.

## Goals / Non-Goals

**Goals:**
- Resource names and ownership decided in one place from the model, with no
  runtime calls, so they are testable without Apple `container`.
- A service plan (selection plus closure, ordered, with notices) that the
  validation in `compose-capability-gate` consumes.
- Behaviour equal to Docker Compose for external, named and dependency cases.

**Non-Goals:**
- Volume drivers, NFS, `driver_opts` (refused by the gate).
- `--scale`, aliases, `compose run`, `down --remove-orphans` semantics.
- Migrating or deleting volumes created by earlier versions.

## Decisions

### D1. `compose_resources.py`: pure resolution

`resolve(project) -> Resources` returns, for every network and volume key, a
`Resource(key, name, external, managed, labels)`. Source of the name, in order:
the normalized model's `name` (official path); otherwise explicit `name:`;
otherwise `key` when external; otherwise `<project>_<key>`. The built-in parser
path therefore produces the same names, and a test compares both paths on the
same file (the existing "Equivalent models" requirement).

*Alternative: keep prefixing and special-case `external` at the call sites.*
Rejected: three call sites already disagree (`_named_volume_args`,
`_service_networks`, `_ensure_network`) and the next one would too.

### D2. Existence check before creation, read-only

After the plan is computed and validated, one pass runs `volume inspect` /
`network inspect` for each external resource used by planned services. Missing
resources fail with exit 1 and the Docker wording ("external volume … not
found"). This runs before `_ensure_network`, so nothing is created. The check
is the only new runtime call and is read-only. For managed resources whose
runtime name already exists, the labels are read in the same pass:
other-project label → refuse; no label → reuse and mark not removable.

### D3. Ownership = model AND label

`cmd_down` computes removable resources as `{managed resources of the current
model} ∩ {label project == this project}`, not "everything labelled". Effects:
a resource whose declaration changed to `external` is protected; a labelled
resource that is not in the file is not removed by `down -v` (Compose does the
same: it removes what the file declares). The resource-key label
(`com.docker.compose.volume` / `.network`) is written on creation to make the
label check specific, and its absence on older resources is accepted.
When `down` runs without a file (label-only mode, `resolved_name()`), the
model is unavailable: keep today's label-only behaviour for networks and
refuse `-v` volume removal without a file (exit 64 with the reason), since the
guarantee depends on the model. This is stricter than today and is covered by a
task and a scenario in `compose-resource-identity`.

*Alternative: also write an "external" marker label.* Rejected: labels can only
be written at creation and external resources are not created by us.

### D4. Legacy copies: notice only

Earlier versions created `<project>_<key>` for external keys. A copy may hold
data, because it was the volume the service used. Deleting it would lose that
data and mounting it would hide the real volume, so the shim prints one notice
per run and does nothing else.

### D5. `compose_plan.py`: selection is a graph query

`plan(project, selected, no_deps) -> Plan(ordered, notices)`:
1. validate names (unknown → exit 1);
2. take `selected` or all services;
3. unless `no_deps`, add dependencies transitively over required entries, and
   over optional ones only if already in the set;
4. order topologically (reuse `Project.topo_sorted` on the induced subgraph);
5. reject cycles, undefined dependencies and required dependencies in inactive
   profiles with the services named.
`cmd_up` consumes `Plan.ordered`; conditions (`_wait_service_healthy`,
`completes`) already work per service, so pulled-in dependencies need no new
code in the wait path. Reconcile (keep/start/recreate) is unchanged, so the
"recreate dependents when a dependency is recreated" rule keeps working.

`pull` and `build` keep acting on the selection only (Compose does the same
unless `--include-deps` / `--with-dependencies` is passed; those flags are not
added here).

### D6. `required: false`

`Service` stores `depends_required: dict[str, bool]` next to
`depends_conditions`. An optional dependency absent from the plan adds nothing;
present in the plan, the ordering and condition apply, but a failed wait is
downgraded to a notice and the dependent is created. `service_healthy` on an
optional dependency without a healthcheck is a notice, not a refusal (the
current `_validate_dependencies` refuses).

### D7. External network attachment

`_service_networks` returns runtime names from `Resources`; `_select_shared_attachment`
and `_inject_etc_hosts` compare runtime names, not `project_key` strings.
Containers on an external network get discovery entries for peers that share it
(Compose scope rules), which `compose-service-discovery` already requires.

## Risks / Trade-offs

- [An external volume that is also written by a stray prefixed copy] → notice
  (D4); no automatic migration; documented in the README migration note.
- [`down -v` without a file now refuses] → previously it removed labelled
  volumes; users run it from the project directory in practice; the error says
  how to proceed. Trade-off accepted: the ownership guarantee needs the model.
- [Planner changes what `up api` creates] → more containers than before, which
  is the Docker behaviour; the change is visible in the output ("Creating …").
- [Existence check races with a concurrent `volume rm`] → the later `run` fails
  with the runtime error, nothing was created by us; acceptable.
- [Apple naming rules for `name:` values] → runtime rejects invalid names with
  its own message, surfaced unchanged.
- [Plan and gate disagree] → one `Plan` object is the input of both; a test
  asserts the validated set equals the created set.

## Migration Plan

1. Land the pure modules and their tests (no behaviour change).
2. Switch name resolution; replace the external-volume unit tests.
3. Switch `cmd_up` to the plan; add `--no-deps`; add the fixtures.
4. Update README. Rollback: revert the commits; managed volume names do not
   change at any step, so data is never orphaned by a rollback.

## Open Questions

- Whether `down` without a file should keep removing labelled networks (kept
  here because networks hold no data).
