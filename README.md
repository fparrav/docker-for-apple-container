# docker-for-apple-container

[![PyPI version](https://img.shields.io/pypi/v/docker-for-apple-container?style=flat-square)](https://pypi.org/project/docker-for-apple-container/)
[![Homebrew](https://img.shields.io/badge/homebrew-tap-orange?style=flat-square)](https://github.com/appautomaton/homebrew-tap)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue?style=flat-square)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS%20%28Apple%20Silicon%29-lightgrey?style=flat-square)](#requirements)
[![Website](https://img.shields.io/badge/website-appautomaton.com-blue?style=flat-square)](https://appautomaton.com/docker-for-apple-container/)

> Run `docker` and `docker compose` on macOS, backed by Apple's native `container` CLI. No Docker Desktop.

`docker-for-apple-container` is a small `docker` command wrapper for Apple's
`container` CLI. It lets tools that expect a `docker` binary run against Apple
`container` on macOS, without installing Docker Desktop, Podman, or a
third-party adapter.

It is a **stateless translator**, not a Docker replacement. It maps each Docker
command to a clean Apple `container` equivalent and fails loudly on the rest.
Apple `container` is the single source of truth, so the shim persists nothing of
its own (no sidecar file, registry, or database). Even `docker compose` stays
stateless: project membership is stored as labels **in Apple's own object
store**, exactly as Docker Compose does, so every verb reconstructs the project
by querying Apple rather than reading shim-owned state.

## Install

Every method below gives you a `docker` command backed by Apple `container`.
Use them on a Mac that runs Apple `container` rather than Docker Desktop.

With Homebrew:

```bash
brew install appautomaton/tap/docker-for-apple-container
```

With uv:

```bash
uv tool install docker-for-apple-container
```

From source, symlink the launcher onto your PATH:

```bash
git clone https://github.com/appautomaton/docker-for-apple-container.git
cd docker-for-apple-container
ln -sf "$(pwd)/bin/docker" ~/.local/bin/docker
```

After any of these, run `docker` as usual. If a tool resolves its Docker binary
from an environment variable or config setting, point that at the installed
`docker` (or the repo's `bin/docker`).

## Requirements

- macOS 26 with Apple `container` 1.5.0 only
- The `container` apiserver running (check with `container system status`)

Only Apple `container` 1.5.0 is supported. There is no backward compatibility
with older releases or implied support for newer ones.
The supported version advances explicitly after validation. The shim depends
on the `container` executable, not directly on Apple's Containerization Swift
package. Apple selects and bundles Containerization as part of `container`, so
there is no separate framework to install or manage.

Nothing else is required. The shim is pure Python standard library with no
third-party packages, and it runs on the Python that ships with macOS. The
official `docker-compose` binary is optional and only widens what Compose files
the shim can read (see [Compose normalizer](#compose-normalizer)).

Start the apiserver with:

```bash
container system start
```

## Supported Docker Subset

Three tiers. Anything outside them fails with an explicit exit-64 error instead
of pretending to work.

Docker's official CLI reference defines the behavior of the subset documented
here, including [`docker container ls`](https://docs.docker.com/reference/cli/docker/container/ls/),
[`docker inspect`](https://docs.docker.com/reference/cli/docker/inspect/),
[`docker container exec`](https://docs.docker.com/reference/cli/docker/container/exec/),
[`docker system df`](https://docs.docker.com/reference/cli/docker/system/df/),
[`docker compose`](https://docs.docker.com/reference/cli/docker/compose/),
and [Docker output formatting](https://docs.docker.com/go/formatting/). Apple
`container --help` and its runtime JSON define which of those behaviors can be
translated faithfully. Unlisted Docker behavior is not implied; when no
verified Apple equivalent exists, the shim refuses it explicitly.

### Fully translated (the core contract)

- `docker version`
- `docker info --format "{{.Driver}}"`
- `docker build -f DOCKERFILE -t TAG CONTEXT`
- `docker image inspect [--platform OS/ARCH] [-f|--format TEMPLATE] IMAGE...`
- `docker images` / `docker image ls` with Docker-shaped default, quiet,
  digest, no-truncation, JSON, and bounded template output
- `docker run -d ... IMAGE CMD...`
- `docker create ... IMAGE CMD...` uses the same flag translation as `run` and
  prints the new container ID
- `docker ps -a --filter ... --format ...` with Docker-shaped default columns,
  JSON lines, and bounded templates. Stateless filters cover `id`, `name`,
  `label`, `status`, `ancestor`, `network`, and `volume`.
- `docker inspect [--type container] [-f|--format TEMPLATE] CONTAINER...`
- `docker container inspect ...` is an alias for `docker inspect`
- `docker port CONTAINER [PRIVATE_PORT[/PROTO]]` and
  `docker container port ...`
- `docker start CONTAINER...`; successful non-attached starts of containers
  carrying Compose labels refresh that project's `/etc/hosts` mappings from
  current runtime addresses. Attach and interactive modes (`-a`/`-i`) require
  exactly one container and remain direct Apple passthroughs.
- `docker exec [OPTIONS] CONTAINER CMD...`, including detach, interactive/TTY,
  user, environment, environment-file, and working-directory options
- `docker stop [-s SIGNAL] [-t N] CONTAINER...`
- `docker rm [-f] CONTAINER`

`docker inspect` supports a deliberate template subset: case-sensitive field
paths, optional whitespace, multiple expressions mixed with literal text, and
`json` rendering such as `{{json .Config.Labels}}` or `{{json .}}`. The
Docker-shaped container object includes identity, image, labels, lifecycle
state and timestamps, process arguments and environment, working directory and
user, mounts, published ports, CPU and memory limits, DNS settings, selected
security settings, and primary and per-network addresses. `--format json`
prints one compact object per requested container. Dictionaries and lists
require `json`; unsupported fields and full Go-template features fail clearly
instead of being guessed.

Image inspection uses the same bounded formatter and selects the requested
platform, or the host platform when none is given. Its Docker-shaped object
includes IDs, tags, repository digests, creation time, size, platform, image
configuration, and root filesystem layers. Image-list templates support
`ID`, `Repository`, `Tag`, `Digest`, `CreatedSince`, `CreatedAt`, and `Size`.

### Translated extras

- `docker logs [-f] [--tail N] CONTAINER`. `--tail N` maps to Apple `-n N`
  (and `--tail all` to "print all"). `--since` and `--timestamps` have no Apple
  equivalent, so they are refused.
- `docker stats [--no-stream] CONTAINER`. Go-template `--format` is refused.
  Apple `--format` accepts only `json|table|yaml|toml`.
- `docker cp SRC DEST`. The positional `container:path` form maps 1:1 onto
  Apple `container copy`. Docker `-a` and `-L` flags are refused.
- `docker restart [-t N] CONTAINER...`, composed from `stop` + `start` (Apple
  has no `restart`). It uses the same Compose-label-based host refresh as
  non-attached `start`. The repair remains best-effort and stateless; a refresh
  failure warns without changing a successful lifecycle status.
- `docker export [-o FILE] CONTAINER` maps onto `container export -o`.
  Note: Apple `container export` requires the container to be **stopped**
  (Docker also exports running ones). The shim surfaces Apple's "container
  is not stopped" error rather than silently stopping it for you.
- `docker login [-u USER] [--password-stdin] SERVER` and `docker logout SERVER`
  delegate to `container registry login/logout`. **Apple stores the
  credential. The shim keeps nothing.** Docker `-p/--password` is refused in
  favor of `--password-stdin`.
- `docker system info` maps to `docker info`. `docker system prune [--volumes]`
  runs Apple's `prune` + `image prune` + `network prune` (+ `volume prune`). It is
  **non-interactive**: there is no confirmation prompt, and `-f`/`-a` are no-ops.
- `docker system df [--format json|table|yaml|toml]` maps directly to Apple's
  Docker-shaped disk-usage report. Go templates and Docker's verbose mode are
  refused because Apple has no faithful equivalent.
- `docker container prune [-f]` maps to Apple's non-interactive stopped-container
  prune. Docker prune filters are refused rather than silently ignored.

### Thin passthrough (basic forms only)

`docker pull`, `docker push`, `docker tag`, `docker save`, `docker load`,
`docker rmi` (the top-level alias for `docker image rm`),
`docker image <sub>` (`pull`/`rm`/`tag`/`push`/`save`/`load`/`prune`),
`docker network <sub>` and `docker volume <sub>`
(`create`/`ls`/`rm`/`inspect`/`prune`), and `docker kill [-s SIG]` forward to the
matching Apple `container` command.
Subcommand names and common flags line up, but Docker-only flags are not
translated. Go-template `--format` on `ls`-style commands is refused rather
than mis-forwarded, and subcommands Apple lacks (e.g. `network connect`) fail
loudly.

### Compose (stateless orchestration)

`docker compose up/down/ps/logs/build/pull/exec/start/stop/restart/rm/config/ls/version`
orchestrate multi-service stacks **without persisting any shim-owned state**.
Apple `container` has no native compose, so the shim issues a sequence of
`container` commands but keeps no project file. Every resource is tagged with
Docker's own label schema (`com.docker.compose.project`,
`com.docker.compose.service`, and so on) on the containers, the project network,
and any named volumes. Runtime verbs reconstruct membership by querying Apple
and filtering on those labels. `up`, `pull`, `build`, and `config` require the
compose file. `start`, `stop`, `restart`, and `rm` use it for dependency order
when available, then fall back to stable service-name order from labels.

- **Project name** resolves like Docker: `-p NAME` → `COMPOSE_PROJECT_NAME` →
  the file's `name:` → the directory basename.
- **Common runtime verbs remain stateless.** `exec` resolves one service to its
  labeled container and defaults to Compose's interactive TTY behavior (`-T`
  disables the TTY). `start` runs dependencies first; `stop` and `rm` run in
  reverse order; `restart` stops in reverse and starts forward. Without a file,
  service-name sorting supplies a deterministic order. `rm` never prompts;
  `-f` is accepted, and `-s/--stop` stops running services before removal.
- **Pull uses declared images.** `compose pull [SERVICE...]` reads the file and
  forwards each selected service image to Apple's image pull. Build-only
  services are skipped with a clear warning instead of being built implicitly.
- **One container per service.** Membership is reconstructed from Compose labels.
  Duplicate service containers or a container number other than 1 fail clearly;
  the shim never guesses which scaled replica to use.
- **Service discovery.** Apple does not resolve service names by DNS without an
  admin `container system dns` domain. The shim closes the gap in two layers,
  both writing `<ip>  <service>` lines only to **each container's own
  `/etc/hosts` file** (ephemeral, discarded with the container — **the macOS
  host's `/etc/hosts` is never touched**):
  - *Boot-time, for dependencies.* Services start in `depends_on` order, so a
    dependent service's dependencies already have known IPs. The shim wraps
    its entrypoint in a `/bin/sh` prelude that writes those lines **before**
    exec'ing the real process — an app that dials its database in its first
    millisecond still resolves the name (post-start injection alone loses
    that race, and Apple has no restart policies to give the app a second
    try). Requires `/bin/sh` in the image; if the wrapped launch fails, the
    shim retries unwrapped. Opt out per service with
    `x-shim-boot-hosts: false`.
  - *Post-start, for all peers.* After everything is up, the full project's
    lines are appended idempotently into every container via `container exec`
    (IPs read live from `container inspect`), covering peers that aren't
    declared dependencies. With multiple networks, each receiver gets a peer's
    address from the first network they share, matching Docker's network-scoped
    service discovery instead of leaking an unrelated interface address.
- **DNS configuration.** Compose `dns`, `dns_search`, and `dns_opt` values are
  forwarded directly to Apple `container run`, preserving scalar or list order.
- **`host.docker.internal`.** The same `/etc/hosts` injection also publishes
  `host.docker.internal` and `gateway.docker.internal` pointing at the
  container's gateway, which on Apple `container` **is the macOS host.** This
  mirrors Docker Desktop (which adds these names automatically on macOS/Windows),
  so a service that dials the host by that name (for example
  `http://host.docker.internal:8317`) works unchanged. The gateway is read
  per-network from `container inspect`, not hardcoded. Injection is idempotent.
  On shell-less images (e.g. distroless, cloudflare/cloudflared) where `exec sh`
  is impossible, it falls back to `container cp`: /etc/hosts is copied out,
  merged, and copied back via the guest agent — only a container that exits
  before injection lands is skipped, with a warning. It is **compose-only**.
  Bare `docker run` is left alone, since injecting into a possibly short-lived
  container would race its exit (Apple has no `--add-host` flag to set it at
  creation, so it must be done via a post-start `exec`).
- **Named volumes** map onto Apple-native volumes (`container volume create`),
  scoped as `<project>_<volume>`. Host-path mounts become bind mounts, with
  relative paths resolved against the compose file's directory.
- **Teardown is self-coherent.** `down` removes the project's containers (found
  by label), then removes the network (and with `-v`, the volumes) **only if the
  shim created them** (verified via the project label), never external ones.
- **`up` is idempotent and converges.** Each container carries
  `com.docker.compose.config-hash` (a SHA-256 over the normalized service
  definition plus the image ID) and `com.docker.compose.image`. Re-running `up`
  keeps an unchanged running container untouched, starts a stopped one, and
  recreates one whose definition or image changed (or that has no hash label),
  together with the services that depend on it, printing what is recreated and
  why. `--force-recreate` recreates everything, `--no-recreate` never recreates
  an existing container (it still creates missing ones); the two are mutually
  exclusive. Containers of services no longer in the file are reported as
  orphans and removed only with `--remove-orphans`. All images are resolved
  before any container is touched, and a failing service is named in the error
  while the services that already started keep running; fixing the file and
  running `up -d` again starts only what is missing.
- **Dependency conditions are enforced.** `depends_on` accepts
  `service_started` (the default), `service_healthy` and
  `service_completed_successfully`; any other condition exits 64. A
  `service_healthy` dependency must declare an enabled `healthcheck` (otherwise
  `up` exits 64 before creating anything). The shim evaluates the check with
  `container exec`, honouring `test` (`CMD`, `CMD-SHELL`), `interval`,
  `timeout`, `retries`, `start_period` and `start_interval` with Compose's
  defaults; the dependent is created only once the check passes. Failures name
  the service, the last check output and the last 40 log lines. A
  `service_completed_successfully` dependency runs in the foreground and `up`
  fails if it exits non-zero. Every wait is bounded (default
  `start_period + retries * (interval + timeout)`) and ends early when the
  container exits; `--wait-timeout SECONDS` overrides the bound.
- **`up --wait`** implies `-d` and returns only when every started service is
  running and, where it declares a healthcheck, healthy; it exits non-zero on
  timeout or when a service exits early. Healthchecks are evaluated only while
  `up` gates a dependent or waits: there is no background monitoring and no
  restart on failure, and `up` warns about a declared healthcheck that nothing
  evaluates.
- **Volumes survive recreation.** Recreating a service never removes a volume.
  `down` removes no volume; `down -v` removes only the named volumes the shim
  created for that project (verified by label), never external or other-project
  volumes.
- **The Compose model comes from the official `docker-compose` when it is
  installed**, and from a small built-in parser otherwise. See
  [Compose normalizer](#compose-normalizer) below; `docker compose version`
  reports which one is active.

Compose keys with no Apple equivalent (the service-level `restart` policy,
`hostname`, `secrets`, `configs`, `deploy` replicas, `extra_hosts`, and network
aliases) are parsed but ignored, with a one-line warning per service so
behavior is never silently misrepresented. Security keys are different:
`privileged`, `security_opt`, `pids_limit`, `sysctls`, and `devices` make `up`
fail with exit 64 before any network or container is created (see
[Security options](#security-options)).
`compose run`, scaling, network aliases, continuous healthchecks and
anonymous-volume removal remain intentionally out of scope.

#### Compose normalizer

The shim does not re-implement Compose. When the official Docker Compose binary
is available it renders the merged, interpolated model with
`docker-compose config --format json`, and the shim only translates that model
into Apple `container` commands. Install it without Docker Desktop:

```bash
brew install docker-compose
```

No Docker Engine is needed: `config` renders the model without contacting one
(verified with Docker Compose 5.6.0, the tested release). The binary is found
through `CONTAINER_DOCKER_SHIM_COMPOSE` (an explicit path that must exist; there
is no fallback after an explicit choice) or else `PATH`. The shim never calls
`docker compose` to normalize and refuses a binary that resolves back to itself.

With the official binary the shim supports repeatable `-f` files merged in
order, the automatic `compose.override.yaml`, `--env-file` and `.env`,
`--profile`, `COMPOSE_FILE`, `COMPOSE_PROFILES`, `COMPOSE_PROJECT_NAME`,
`--project-directory`, YAML anchors, aliases, merge keys, `|`/`>` block scalars,
`extends`, `include`, `env_file` and relative paths exactly as Compose resolves
them. `docker compose config` prints the normalized JSON and `config -q` only
validates it.

`docker compose version` prints `normalizer: official <version>` or
`normalizer: built-in subset`. Switching normalizer changes the model's
fingerprint, so the next `up` may recreate containers once.

Without the official binary the built-in parser reads only block maps and
sequences, flow collections, quoted scalars, comments and `${VAR:-default}`
interpolation. It **refuses, with exit 64 and the install hint**, everything it
cannot read instead of misreading it: anchors, aliases, merge keys, `|`/`>`
block scalars, multi-document files, `include`, `extends` and `profiles`; several
`-f` files, `--profile`, `COMPOSE_PROFILES` and an automatically merged override
file are refused as well.

### Refused, by design

Commands and flags with no verified Apple equivalent fail loudly:
`docker system events` (a stateful watcher), `docker commit`/`diff`/`rename`/
`history`/`import` (no Apple equivalent), `docker run --network=none`,
`docker run --add-host/--hostname`, and any unknown command.

### Security options

Restrictions are applied or refused, never dropped silently. Each option below
has exactly one status; the table is generated from `security.RULES` and a test
keeps it in sync.

| Option | Status | Reason |
| --- | --- | --- |
| `cap-add` | SUPPORTED | names normalized to CAP_* by the runtime |
| `cap-drop` | SUPPORTED | names normalized to CAP_* by the runtime |
| `read-only` | SUPPORTED | verified by a failing write |
| `user` | SUPPORTED | forwarded unchanged |
| `memory` | SUPPORTED | granularity of 1 MiB |
| `cpus` | SUPPORTED | forwarded as a fractional CPU count |
| `ulimit` | SUPPORTED | forwarded as --ulimit |
| `tmpfs` | SUPPORTED | options applied by the runtime |
| `init` | SUPPORTED | forwarded unchanged |
| `security-opt` | UNSUPPORTED | Apple container 1.5.0 has no equivalent: no seccomp/AppArmor/no-new-privileges knob is documented |
| `pids-limit` | UNSUPPORTED | Apple container 1.5.0 documents no process-count limit |
| `privileged` | UNSUPPORTED | Apple container 1.5.0 has no privileged mode |
| `storage-opt` | UNSUPPORTED | Apple container 1.5.0 documents no storage driver options |
| `sysctls` | UNSUPPORTED | Apple container 1.5.0 documents no sysctl passthrough |
| `devices` | UNSUPPORTED | Apple container 1.5.0 documents no host device passthrough |
| `hostname` | UNSUPPORTED | not a restriction; Apple container 1.5.0 documents no hostname flag |
| `add-host` | UNSUPPORTED | not a restriction; Apple container 1.5.0 documents no add-host flag |
| `network-none` | UNSUPPORTED | not a restriction; Apple container 1.5.0 has no none network |

A command that uses an UNSUPPORTED option exits 64 before any container is
created, naming the option and the reason. The same applies to the matching
Compose keys (`privileged`, `security_opt`, `pids_limit`, `sysctls`, `devices`),
which are validated for every service before `up` creates anything.

To run a workload whose restriction cannot be enforced, opt in explicitly by
naming the option in `CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED` (comma-separated;
there is no wildcard). Allowed names: `security-opt`, `pids-limit`,
`storage-opt`, `privileged`, `sysctls`, `devices`. The option is skipped with a
warning on every use and the restriction is **not** enforced:

```bash
CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED=security-opt,pids-limit docker run ...
```

### Caveats

- `docker run -v host:ctr:ro` becomes an Apple `--mount` bind (only `ro` mode is
  honored). `--tmpfs` values, including their mount options, are forwarded to
  Apple `container` unchanged.

## State

The shim is stateless. It does not persist Docker-shaped metadata, cache files,
or a support directory. Apple `container` is the source of truth, so direct
Apple container changes are reflected on the next shim command. Compose is no
exception: project bookkeeping lives in Apple's label store, not in any
shim-owned file. See the Compose section above.

## Tests

Unit tests use a fake `container` binary and do not start real containers. The
suite separates generic CLI and inspect behavior from focused consumer
contracts, while compose tests cover parsing, interpolation, dependency order,
translation, and label-based orchestration:

```bash
python3 -m unittest discover -s tests -v
```

Live smoke testing against Apple `container` is intentionally manual because it
starts and removes containers. The current development and test-fixture
baseline is Apple `container` 1.5.0.
