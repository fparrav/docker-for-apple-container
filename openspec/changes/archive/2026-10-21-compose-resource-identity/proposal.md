# Proposal

## Why

Two defects break data and availability in ordinary Compose projects. First,
an `external: true` volume or network is looked up as `<project>_<name>`: the
shim creates a project-labelled copy, mounts the copy and `down -v` deletes the
copy, so the real PostgreSQL data is never used, and the existing unit test
asserts exactly that behaviour. Second, `docker compose up api` filters the
service list before following `depends_on`, so the database `api` needs is not
created, not waited for, and its `service_healthy` gate never runs. See
`findings.md` (3.1 to 3.5, 4.1 to 4.4) in `compose-capability-gate`.

## What Changes

- Resolve every network and volume to one runtime identity from the normalized
  model: managed resources get `<project>_<key>`, an explicit `name:` is
  honoured, and `external: true` resources use their own name without prefix.
- Label every managed resource with Compose's own project and resource-key
  labels, and make `down`/`down -v` remove a resource only when it is managed
  by this project in the current model and carries this project's label.
- Check external resources exist before creating anything and fail with a clear
  error when they do not; never create, relabel or delete them.
- Plan `up` from the selection plus the transitive closure of `depends_on`,
  in dependency order, validate and create exactly that set, and support
  `--no-deps`.
- Honour `depends_on.required: false` and report dependencies that sit in an
  inactive profile.
- Replace the unit test that encodes the external-volume defect and add
  PostgreSQL fixtures that prove data survives, is reused and is never deleted.
- **BREAKING (bug fix)**: a project that declared an external volume or
  network now uses the real resource; an empty prefixed copy created by earlier
  versions is reported once and left alone.

## Capabilities

### New Capabilities

- `compose-resource-identity`: how volumes and networks are named, labelled,
  adopted, protected and removed, for managed, external and explicitly named
  resources.
- `compose-service-selection`: which services `up` creates for a selection,
  the dependency closure, ordering, `--no-deps`, optional dependencies and
  profile interaction.

### Modified Capabilities

- `compose-volume-safety`: external and explicitly named volumes use their real
  names, and ownership is decided by the current model plus labels.
- `compose-dependency-conditions`: `required: false` semantics and the
  evaluation of conditions on dependencies pulled in by a selection.

## Impact

- Code: new `src/container_docker_shim/compose_resources.py` (identity and
  ownership) and `compose_plan.py` (selection and closure) as pure modules;
  `compose.py` (`_network_resource_name`, `_volume_resource_name`,
  `_named_volume_args`, `_ensure_network`, `_ensure_named_volume`, `cmd_up`,
  `cmd_down`, `_service_networks`, `_select_shared_attachment`,
  `_parse_depends_conditions`).
- Tests: `tests/test_compose.py` (replace the external-volume cases),
  new `tests/test_compose_resources.py`, `tests/test_compose_plan.py`, live and
  conformance fixtures O and P, matrix entries for `up --no-deps`,
  `depends_on required`, `volumes external/name`, `networks external/name`.
- Docs: README Compose section ("Named volumes", "Teardown"), `docs/testing.md`.
- Compatibility: Compose files do not change. Behaviour now matches Docker
  Compose; `up <svc>` creates more containers than before (its dependencies).
- Out of scope: scaling, network aliases, `compose run`, volume drivers
  (refused by `compose-capability-gate`), any change to the labels the shim
  already writes on containers.
- Regression risk: volume names change for external volumes only; managed
  volumes keep `<project>_<key>`, so existing projects keep their data.
- Depends on `compose-capability-gate` for validation of the planned set; the
  planner can land first because it only changes which services are chosen.
