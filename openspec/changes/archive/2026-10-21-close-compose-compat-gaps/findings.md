# Findings

Evidence gathered on 2026-10-21 with `container` 1.5.0, `docker-compose` 5.6.0 and
Docker Desktop 29.9.1 (conformance only). Anything created was disposable and
removed by exact name.

## F1. Scan of `~/source-code` with the shim's gate

A throwaway script called `compose._load_project` and `compose._validate_plan`
on each of the 39 Compose files outside this repository (no runtime call).

| Outcome | Files | Cause |
| --- | --- | --- |
| Accepted (only `NOT APPLIED` restart/logging notes) | 12 | none |
| Refused, exit 64 | 4 | `- /app/node_modules` anonymous volumes (TechSuite dev, two copies); `driver_opts` on volumes and a network (Thingboard); `network_mode: host` (`orquestador-demo`) |
| Not judged, exit 1 | 23 | the normalizer failed before the gate: missing `.env`/`secrets.env`, required variable unset, or a partial file meant to be combined with another (as Docker Compose would also fail) |

The "not judged" group is the case for `--dry-run`: the gate cannot be run
without `up` today, so the answer for a project that depends on user-provided
variables is unknowable without side effects.

## F2. Anonymous volumes on Apple `container` 1.5.0

- `container run -v /etc/apk alpine:3.20 ...` is accepted and creates a volume
  whose `TYPE` is `anonymous` with a UUID name.
- `container rm -f` leaves that volume behind (it had to be removed by name).
- A new volume (named or anonymous) is empty apart from `lost+found`: the image's
  content at the mount target is **not** copied into it. Docker copies it on first
  use, so `- /app/node_modules` (the usual way to keep an image's installed
  dependencies under a bind-mounted source tree) would start with an empty
  directory.
- The official normalizer emits `{"type": "volume", "target": "/x", "volume": {}}`
  (or without `volume`), with no `source`, for both `- /x` and the long form.

Decision (taken with the user): support anonymous volumes with first-use
population (copy-up), instead of leaving them refused or empty.

## F3. `driver_opts` of the `local` driver

Thingboard declares six volumes as `driver: local` + `driver_opts: {type: none,
o: bind, device: /opt/...}`. That is Docker's way to name a bind mount; it needs
no volume object. The normalized model keeps it as
`{"driver": "local", "driver_opts": {"device": ..., "o": "bind", "type": "none"}}`.
Its network uses `com.docker.network.bridge.name: tb-voxcom-br`, which only names
a Linux bridge.

## F4. `network_mode`

`network_mode: host` (`orquestador-demo`) cannot be honoured: Apple `container`
has no host network, and replacing it by the project network would change which
addresses the service can reach. Nothing safe can be executed, so the refusal
stays non-waivable; only the message can be made useful (published ports and
`host.docker.internal`, which the shim already injects).

## F5. Runtime validator exit status

`scripts/validate-runtime.py` returns 0 when `run_live` finds no executed test
(runtime down, every test skipped): `problems` only counts `FAILED` tests and
missing Apple flags. The `.github/workflows/runtime.yml` job therefore passes
with "live tier: not run". `test-tiers` says a skipped tier must be reported as
not run, but nothing makes the exit status reflect it.

## F6. `uv tool install` syntax

The documented command

```
uv tool install --from docker-for-apple-container-fork \
    git+https://github.com/fparrav/docker-for-apple-container@vX.Y.Z
```

fails with `Package requirement (docker-for-apple-container-fork) provided with
--from conflicts with install request (git+...)`. Verified in an isolated
`UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` with uv 0.9.17. Both of these work and install
the `docker` executable:

```
uv tool install --from git+<url>@<tag> docker-for-apple-container-fork
uv tool install "docker-for-apple-container-fork @ git+<url>@<tag>"
```

The same wrong command is in `docs/releasing.md`. `pipx install git+...@<tag>`
is correct.

## F7. Same gap for named volumes (out of scope)

Because of F2, a **named** volume that a service mounts over image content (an
nginx `html` volume, for example) also starts empty on Apple and populated on
Docker. It is existing behaviour of this shim; fixing it would change what
running stacks see, so it is recorded here and not changed by this change.
`lost+found` at the root of every Apple volume is a related, pre-existing
difference.

## F8. `n-network-none` on Docker Desktop

`ls /sys/class/net` inside a `network_mode: none` container lists `bonding_masters`,
`erspan0`, `gre0`, `gretap0`, `ip6_vti0`, `ip6gre0`, `ip6tnl0`, `ip_vti0`, `lo`,
`sit0`, `tunl0` on Docker Desktop's kernel: placeholders from loaded modules, not
interfaces. The 17 other conformance tests pass; this one asserts the wrong
thing. Neither it nor `/proc/net/dev` is usable (the kernel creates those down tunnel devices in every namespace); `ip -o addr` lists only `lo` for `--network none` and adds `eth0` on the default network.

## F9. The validator cannot succeed against the supported runtime

Found while verifying F5: `scripts/validate-runtime.py --skip-live` against
`container` 1.5.0 reported three problems (`docker version`, `docker version
--format json`, `docker version --format version templates`) and exited 1 before
this change. Their matrix field `apple` is `container --version; container system
status`; the validator treated the whole string as one command and ran
`container --version; container system status --help`, which fails. The runtime
has both pieces (`--version` is a global option; `container system status
--help` exits 0). With the exit statuses of F5 fixed, the same defect would make
the runtime CI job fail permanently for a reason unrelated to the runtime.
