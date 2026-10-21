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

A fixture can describe something the shim must refuse. `n-network-none` asks for
`network_mode: none`: the live tier asserts `up` exits 64 with a `REFUSED:` line
and creates nothing, while the conformance tier asserts real Docker starts the
service with only the loopback interface. `m-readonly-volume` mounts a named
volume and a bind read-only in the short (`:ro`) and the long (`read_only: true`)
syntax, plus a read-write control, and both tiers assert that the four writes
fail and the control succeeds. `r-selinux-refused` asks for `:Z`: the live tier
asserts `up` exits 64 with a `REFUSED:` line naming SELinux and creates nothing,
while the conformance tier asserts real Docker accepts the file (the documented
difference).

Two fixtures cover resource identity and service selection.
`o-external-volume` declares a volume `external: true` with `name: ${EXT_VOLUME}`;
the test pre-creates a disposable `shimtest-` volume, passes its name through a
`.env` override, and asserts the data survives `down -v` and a second `up` and
that no `<project>_` copy is created. `p-selective-up` has `api` depending on
`db` (healthy) plus an unrelated `worker`: `up -d api` must create `db` and
`api` only, after `db` is healthy, and `up -d --no-deps api` creates `api` alone.

`q-build-secrets` builds an image whose Dockerfile reads a file secret and an
environment secret renamed through `target` with `RUN --mount=type=secret`. The
Dockerfile carries only the SHA-256 of each dummy value, because the build log
echoes every `RUN` line. The environment secret reaches the build only through
the process environment (`Q_ENV_SECRET`), the image name arrives through a
`.env` override, and the test removes the image by its exact name. The live
tier also asserts that `up` exits 64 naming the variable, and creates nothing,
when `Q_ENV_SECRET` is not exported.

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
