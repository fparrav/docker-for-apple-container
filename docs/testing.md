# Testing

The suite separates tests by **what they prove**. A passing run always says
which of these statements it covers:

| Tier | Proves | Needs | Runs by default |
| --- | --- | --- | --- |
| `unit` | A pure function behaves as specified (parsers, option rules, hashing) | nothing | yes |
| `fake` | The shim translates a Docker command into the right Apple `container` calls, against a fake `container` binary | nothing | yes |
| `live` | The command works on a real Apple `container` runtime | macOS 26 on Apple silicon, `container` 1.5.0 running | no, opt-in |
| `conformance` | The same fixture behaves the same on a real Docker Engine | a real Docker Engine client | no, opt-in |

A `fake` pass is **syntactic** compatibility only. A `live` pass is **local
execution** on Apple `container`. Neither says anything about Docker Engine;
only `conformance` does. Every test module declares its tier with a
module-level `TIER = "unit" | "fake" | "live" | "conformance"` line.

## Running

```bash
# unit + fake tiers (the default; starts no container)
python3 -m unittest discover -s tests -v

# add the live tier (needs a running apiserver at the supported version)
SHIM_TIERS=live CONTAINER_DOCKER_SHIM_CONTAINER=/opt/homebrew/bin/container \
  python3 -m unittest discover -s tests -v

# add the conformance tier (a real Docker Engine client, not this shim)
SHIM_TIERS=conformance SHIM_CONFORMANCE_DOCKER=/usr/local/bin/docker \
  python3 -m unittest discover -s tests -v
```

`SHIM_TIERS` is a comma-separated list (`live,conformance`). A tier that is
enabled but cannot run (the apiserver is down, the release differs from the
supported one, no Engine answers `info`) is reported as **skipped with the
reason**, never as passed. A report that did not run a tier must say
"live tier: not run".

## Safety rules for live tests

- Every object is named `shimtest-<topic>-<8 hex>` and removed by its exact
  name in `addCleanup`, including when the test fails.
- Cleanup lists objects by that prefix (or by a label) and never calls any
  prune. Objects without the prefix are never touched.
- Images are pinned (`alpine:3.20`, `nginx:1.27-alpine`, `postgres:17`,
  `redis:7-alpine`). The live tier states when an image has to be pulled; the
  first run therefore needs network access and some disk.

## Fixtures

Reusable, plain Compose files and Dockerfiles live in
`tests/fixtures/compat/<letter>-<name>/`. Their first lines state the tiers and
images they need:

```yaml
# tiers: live, conformance
# images: nginx:1.27-alpine
```

The live and conformance tiers use the **identical, unmodified** file, which is
the point of the conformance tier.

## Conformance tier in CI

The conformance tier needs a Docker Engine, so it is meant for CI or on-demand
runs, never for normal development. On a runner that has Docker:

```yaml
- run: SHIM_TIERS=conformance SHIM_CONFORMANCE_DOCKER=$(which docker) python3 -m unittest discover -s tests -v
```

`SHIM_CONFORMANCE_DOCKER` must point at a real Docker client, because on a Mac
with this project installed `docker` is the shim itself. Hosted macOS runners
have no virtualization, so the `live` tier cannot run there; only `unit` and
`fake` do.

## Advancing the supported runtime

The shim supports exactly one Apple `container` release,
`SUPPORTED_CONTAINER_VERSION` in `src/container_docker_shim/cli.py`. Moving to
a newer release is a deliberate change, never an automatic one:

1. Install the candidate release on a macOS 26 machine with a running apiserver
   (`container system start`).
2. Run the validator. It never edits the supported version:

   ```sh
   scripts/validate-runtime.py --container /path/to/new/container
   ```

   The read-only part asks the candidate's own `--help` whether every Apple
   command and flag named in `tests/compat/matrix.json` still exists. The live
   part runs `SHIM_TIERS=live` against the candidate (disposable `shimtest-`
   objects only) and maps each result back to the matrix entries that cite it.
   Use `--skip-live` for the read-only check alone. The output is a per-entry
   evidence table; a tier that did not run is reported as "not run", never as
   passed.
3. Fix every entry the validator reports (`missing`, `FAILED`): update the
   translation, its tests and the matrix entry together.
4. Only when the validator reports no problems, change
   `SUPPORTED_CONTAINER_VERSION`, the test fixtures and the README together,
   then refresh the published website as described in `AGENTS.md`.

## CI limitation

Hosted CI runners (GitHub-hosted macOS) do not expose virtualization, so Apple
`container` cannot start there: the `live` tier and `scripts/validate-runtime.py`
cannot run in hosted CI. Only the `unit` and `fake` tiers do, and they are the
default `python3 -m unittest discover -s tests -v` run. The `live` tier and the
runtime validation therefore run on a developer Mac (or a self-hosted macOS 26
runner with virtualization), and a release is not advanced on hosted CI alone.
