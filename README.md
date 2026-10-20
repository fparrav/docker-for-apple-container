# docker-for-apple-container-fork

[![Python](https://img.shields.io/badge/python-3.9%2B-blue?style=flat-square)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS%20%28Apple%20Silicon%29-lightgrey?style=flat-square)](#requirements)

> Run `docker` and `docker compose` on macOS, backed by Apple's native `container` CLI. No Docker Desktop.

**Fork of [appautomaton/docker-for-apple-container](https://github.com/appautomaton/docker-for-apple-container).**
This repository is maintained independently: releases are built from its Git
tags and published as its GitHub Releases. It does not use the upstream
package index page, Homebrew tap, or website, and nothing here installs from
or publishes through upstream infrastructure.

`docker-for-apple-container-fork` is a small `docker` command wrapper for
Apple's `container` CLI. It lets tools that expect a `docker` binary run
against Apple `container` on macOS, without installing Docker Desktop, Podman,
or a third-party adapter.

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

### From a release tag

Releases are GitHub Releases of this repository. Install a release tag
(replace `vX.Y.Z` with the tag you want) with `uv`:

```bash
uv tool install --from docker-for-apple-container-fork \
    git+https://github.com/fparrav/docker-for-apple-container@vX.Y.Z
```

or with `pipx`:

```bash
pipx install git+https://github.com/fparrav/docker-for-apple-container@vX.Y.Z
```

`--from` names the distribution explicitly because the repository is named
like the upstream project; both commands install
`docker-for-apple-container-fork`. The executable lands in the tool bin
directory (`~/.local/bin` by default for both `uv` and `pipx`).

### From source

```bash
git clone https://github.com/fparrav/docker-for-apple-container.git
cd docker-for-apple-container
uv tool install .
```

A source install reports a development version, never a release version.

### Verify

```bash
docker version
```

prints the Apple `container` version and a `Shim:` line with this project's
version (the release tag, or a development version from a source install).

### Which `docker` runs

More than one `docker` can sit on `PATH` (this fork, the upstream package, a
source checkout, Docker Desktop). Reveal the resolution order:

```bash
command -v -a docker
```

prints every match in the order the shell resolves it; the first line is what
runs. Put this fork first by reordering `PATH` in your shell profile, or
remove the others (below).

### Agents with a minimal environment

Agents and CI runners often start with a minimal environment (no shell
profile, so no custom `PATH`). Point them at the installed executable by
absolute path:

```bash
~/.local/bin/docker version
```

or extend `PATH` explicitly:

```bash
PATH="$HOME/.local/bin:$PATH" docker version
```

If a tool resolves its Docker binary from an environment variable or config
setting, point that at the same absolute path.

### Switching from the upstream package

If you installed the upstream `docker-for-apple-container` (from its package
index or Homebrew tap), remove it first — `uv` refuses to overwrite an
existing `docker` executable and `pipx` only warns:

```bash
uv tool uninstall docker-for-apple-container
# or: pipx uninstall docker-for-apple-container
# or: brew uninstall docker-for-apple-container
```

Then install this fork with one of the commands above and confirm with
`command -v -a docker` that the fork's path resolves first.

## Requirements

- macOS 26 with Apple `container` 1.5.0 only
- The `container` apiserver running (check with `container system status`)

There is no backward compatibility with older releases and no implied support
for newer ones; the supported version advances explicitly after validation.
The shim depends on the `container` executable, not directly on Apple's
Containerization Swift package. Apple selects and bundles Containerization as
part of `container`, so there is no separate framework to install or manage.

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
- `docker build -f DOCKERFILE -t TAG CONTEXT`, including `--build-arg`,
  `--target`, `--label`, `--platform`, `--no-cache`, `--pull` and `--secret`;
  `--ssh` accepts only `default`. Options with no Apple equivalent such as
  `--cache-from` or `--push` exit 64 naming the option. Dockerfiles and Compose
  files are never modified or rewritten.
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
- **Named volumes and networks** map onto Apple-native resources
  (`container volume create`, `container network create`). A managed one is
  named `<project>_<key>` unless the file gives `name:`, which is used as
  written. An `external: true` resource is used by its own name (`name:` or the
  key): the shim never creates, relabels or removes it, and `up` exits 1 naming
  it, before creating anything, when it does not exist. A managed name that
  already exists under another project's label also exits 1; one without any
  Compose label is reused with a notice and never removed. Host-path mounts
  become bind mounts, with relative paths resolved against the compose file's
  directory.
- **Migrating external volumes.** Earlier versions created and mounted
  `<project>_<key>` even for an `external: true` volume, so the data of such a
  stack may sit in that copy. The shim now mounts the real external volume and
  prints a notice naming the old copy; it neither deletes nor mounts it. Copy
  what you need into the external volume, then remove the old one yourself
  (`container volume rm <project>_<key>`).
- **Teardown is self-coherent.** `down` removes the project's containers (found
  by label), then the networks (and with `-v`, the volumes) that the current
  Compose file declares as managed **and** that carry the project label. External
  resources, resources the file no longer declares, and other projects' resources
  are never removed. `down -v` needs the Compose file to know what the project
  owns: without one it exits 64 and removes nothing (plain `down` still works
  from labels).
- **`up [SERVICE...]` acts on the selection and what it needs.** The named
  services are created together with their `depends_on` closure, dependencies
  first, and unrelated services are left alone; unknown names exit 1 and
  cycles or dependencies on undefined services exit 64, all before anything is
  created. `--no-deps` creates only the named services and prints a notice
  listing the dependencies it skipped. A dependency that sits in an inactive
  profile is reported with the profile that enables it.
- **Optional dependencies.** `depends_on` entries with `required: false` are not
  pulled into the selection; when their service is part of the plan they still
  order and gate their dependent, but a dependency that never becomes healthy,
  or a `service_healthy` gate on a service without a healthcheck, is a notice
  instead of an error.
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
  `down` removes no volume; `down -v` removes only the managed volumes the Compose
  file declares and that carry the project label, never external or
  other-project volumes.
- **The Compose model comes from the official `docker-compose` when it is
  installed**, and from a small built-in parser otherwise. See
  [Compose normalizer](#compose-normalizer) below; `docker compose version`
  reports which one is active.

Before `up` or `build` creates anything, the shim classifies every key of the
normalized model against one capability table (`compose_capabilities.py`) and
reports every problem in a single run. A key is applied, reported, or refused:

- `REFUSED:` (standard error, exit 64): the key would weaken a restriction or an
  isolation request the shim cannot honour, or it is unknown. Nothing is created.
  Restrictions can be waived by naming the key in
  `CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED`; `network_mode` (every value),
  `secrets`, `configs`, and unsupported volume or resource attributes cannot.
- `NOT APPLIED:` (standard error): the key changes behaviour but not isolation
  or data safety, and Apple `container` has no equivalent. The service still
  starts; standard output carries only the Compose progress.

**Breaking change:** projects that used to run with these keys silently ignored
(`network_mode`, `secrets`, `configs`, unknown keys, unsupported build or volume
options) now exit 64. Remove the key, or for waivable restrictions name it in
`CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED`. Read-only volumes and bind mounts are
translated to `--mount ...,readonly` and `build.target`, `labels`, `no_cache`,
`pull`, `secrets` and `ssh: default` reach the build.

Compose keys that change behaviour but have no Apple equivalent (the
service-level `restart` policy, `hostname`, `deploy` replicas, `extra_hosts`,
and network aliases) are accepted, and `up` prints one `NOT APPLIED:` line per
key on standard error so behavior is never silently misrepresented. Security
keys are different: `privileged`, `security_opt`, `pids_limit`, `sysctls`, and
`devices` make `up` fail with exit 64 before any network or container is
created (see [Security options](#security-options)). `secrets` and `configs` are
refused the same way and cannot be waived: they are Compose features, not
Swarm-only ones, and Apple `container` 1.5.0 cannot mount a single file, so the
shim has no safe way to deliver them. The shim never opens the files they name.
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

### Tools that need the Docker socket or Engine API

The shim is a command-line translator. It exposes **no `/var/run/docker.sock`,
no Engine API and no Docker daemon**, and it does not fake one. Anything that
talks to the socket or the API instead of running the `docker` binary is
unsupported: Docker SDKs and Testcontainers-style libraries that connect over
the socket, Portainer, Traefik's Docker provider, `docker-in-docker`, tools that
mount the socket into a container, Docker Swarm and `docker context` remotes.
`docker info` reports only what Apple `container` provides and never claims
Engine features such as Swarm.

### Behavior for scripts and agents

The `docker` executable works when launched as a subprocess with a minimal
environment (`PATH` and `HOME` only, no aliases or shell profile) and what a
caller observes follows Docker's conventions:

- Standard input, standard output and standard error of the executed command
  are propagated unchanged and stay separate, so `docker exec -i c cat` echoes
  its input and container stderr never lands on stdout.
- `docker run`, `docker exec` and `docker compose exec` return the container
  process's exit code (`docker exec c sh -c 'exit 7'` exits 7).
- Termination signals sent to the shim during an attached command are forwarded
  to the underlying `container` process, so interrupting `docker run` leaves no
  orphan behind.
- `docker version`, `docker info`, `docker ps --format`, `docker inspect
  --format`, `docker images --format`, `docker logs` and
  `docker compose ps --format json|table` keep the documented output shapes;
  `docker compose ps` refuses Go templates and formats other than `json` or
  `table` with exit 64, and `docker compose ps --format json` refuses with
  exit 64 a listing that includes an exited container, because the Apple
  runtime exposes no numeric exit code (the default table, `-q` and
  `--services` keep working). The
  contracts of the named consumers (Hermes and OpenClaw) are pinned by tests.
- An unsupported command or option exits 64 with a message on standard error
  that names it and the reason (`docker commit c img` names `commit`), and
  prints nothing resembling success on standard output.

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
`NOT APPLIED:` line on standard error on every use and the restriction is
**not** enforced. Isolation requests (`hostname`, `add-host`, `network-none`,
and Compose `network_mode`) cannot be waived: naming them has no effect, and
the shim prints a warning that the name matches no waivable option:

```bash
CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED=security-opt,pids-limit docker run ...
```

### Compatibility matrix

Every `docker run` and `docker build` option and every Compose service key the
shim knows about has an entry in `tests/compat/matrix.json`. The table below is
generated from it by `scripts/render_compat_table.py --write README.md` and a
test fails when it drifts. `status` says whether the shim honors the construct
(`SUPPORTED`), honors part of it (`PARTIAL`) or refuses it (`UNSUPPORTED`);
`evidence` is the strongest tier that exercises it (`unit`, `fake`, `live`,
`conformance`; see `docs/testing.md`).

Success on Apple `container` does not imply Docker Engine compatibility: a
command that works here can still behave differently from the Docker Engine
(for example, `container image push` always publishes an OCI index and there is no Engine
API). Known differences are called out in the `note` column.

<!-- compat-matrix:start -->
| id | status | apple | evidence | note |
| --- | --- | --- | --- | --- |
| docker build | SUPPORTED | container build | live | Translates the -f/-t build form to Apple container build. |
| docker commit | UNSUPPORTED | - | fake | No Apple equivalent; refused with exit 64. |
| docker compose build | SUPPORTED | - | fake | Builds each service with Apple container build; no shim state persists. |
| docker compose config | PARTIAL | - | fake | The built-in parser refuses anchors, aliases, and block scalars without the official docker-compose binary. |
| docker compose down | SUPPORTED | - | live | Removes only containers, networks, and volumes the shim created, verified by label. |
| docker compose exec | SUPPORTED | - | live | Resolves one service to its labeled container; defaults to an interactive TTY. |
| docker compose logs | SUPPORTED | - | live | Streams each service's logs via Apple container logs. |
| docker compose ls | SUPPORTED | - | fake | Lists projects reconstructed from Compose labels. |
| docker compose ps | SUPPORTED | - | live | Reconstructs project membership from Compose labels. |
| docker compose pull | PARTIAL | - | fake | Build-only services are skipped with a warning instead of being built implicitly. |
| docker compose restart | SUPPORTED | - | live | Stops in reverse dependency order, then starts forward. |
| docker compose rm | SUPPORTED | - | fake | Never prompts; -f is accepted and -s/--stop stops services first. |
| docker compose start | SUPPORTED | - | fake | Starts dependencies first, falling back to service-name order without a file. |
| docker compose stop | SUPPORTED | - | fake | Stops services in reverse dependency order. |
| docker compose up | PARTIAL | - | live | No background monitoring or restarts; every key is validated first, and keys with no Apple equivalent are refused (REFUSED) or reported (NOT APPLIED). |
| docker compose version | SUPPORTED | - | fake | Reports the active normalizer (official docker-compose or built-in subset). |
| docker container inspect | SUPPORTED | container inspect | fake | Alias for docker inspect. |
| docker container port | SUPPORTED | container inspect | fake | Alias for docker port; published bindings come from Apple inspect. |
| docker container prune | PARTIAL | container prune | fake | Non-interactive Apple prune; Docker prune filters are refused. |
| docker cp | PARTIAL | container copy | fake | Maps 1:1 onto Apple container copy; Docker -a and -L are refused. |
| docker create | SUPPORTED | container create | fake | Same flag translation as run; prints the new container ID. |
| docker diff | UNSUPPORTED | - | fake | No Apple equivalent; refused with exit 64. |
| docker exec | SUPPORTED | container exec | fake | Covers detach, interactive/TTY, user, environment, and working-directory options. |
| docker export | PARTIAL | container export | fake | Apple requires the container to be stopped. |
| docker history | UNSUPPORTED | - | fake | No Apple equivalent; refused with exit 64. |
| docker image inspect | SUPPORTED | container image inspect | fake | Selects the requested platform, or the host platform when none is given. |
| docker image load | PARTIAL | container image load | fake | pull/rm/tag/push/save/load/prune forward 1:1; Go-template --format is refused. |
| docker image ls | SUPPORTED | container image list | fake | Docker-shaped default, quiet, digest, no-truncation, JSON, and bounded template output. |
| docker image prune | PARTIAL | container image prune | fake | pull/rm/tag/push/save/load/prune forward 1:1; Go-template --format is refused. |
| docker image pull | PARTIAL | container image pull | fake | pull/rm/tag/push/save/load/prune forward 1:1; Go-template --format is refused. |
| docker image push | PARTIAL | container image push | fake | pull/rm/tag/push/save/load/prune forward 1:1; Go-template --format is refused. |
| docker image rm | PARTIAL | container image rm | fake | pull/rm/tag/push/save/load/prune forward 1:1; Go-template --format is refused. |
| docker image save | PARTIAL | container image save | fake | pull/rm/tag/push/save/load/prune forward 1:1; Go-template --format is refused. |
| docker image tag | PARTIAL | container image tag | fake | pull/rm/tag/push/save/load/prune forward 1:1; Go-template --format is refused. |
| docker images | SUPPORTED | container image list | fake | Top-level alias for image ls with the same output shapes. |
| docker import | UNSUPPORTED | - | fake | No Apple equivalent; refused with exit 64. |
| docker info | SUPPORTED | container system status | fake | Only the {{.Driver}} --format template is translated; other formats are refused. |
| docker inspect | SUPPORTED | container inspect | fake | Bounded template subset: field paths, literal text, and json rendering. |
| docker kill | PARTIAL | container kill | fake | Forwards to Apple container kill, including -s SIG. D1 option semantics: kill -s; see tests/option_semantics.py. |
| docker load | PARTIAL | container image load | fake | Basic form only; Docker-only flags are not translated. |
| docker login | PARTIAL | container registry login | fake | Apple stores the credential; -p/--password is refused in favor of --password-stdin. |
| docker logout | PARTIAL | container registry logout | fake | Delegates to Apple registry logout; flags are refused. |
| docker logs | PARTIAL | container logs | fake | --tail maps to Apple -n; --since and --timestamps are refused. |
| docker network create | PARTIAL | container network create | fake | create/ls/rm/inspect/prune forward; subcommands Apple lacks fail loudly. |
| docker network inspect | PARTIAL | container network inspect | fake | create/ls/rm/inspect/prune forward; subcommands Apple lacks fail loudly. |
| docker network ls | PARTIAL | container network list | fake | create/ls/rm/inspect/prune forward; subcommands Apple lacks fail loudly. |
| docker network prune | PARTIAL | container network prune | fake | create/ls/rm/inspect/prune forward; subcommands Apple lacks fail loudly. |
| docker network rm | PARTIAL | container network delete | fake | create/ls/rm/inspect/prune forward; subcommands Apple lacks fail loudly. |
| docker port | SUPPORTED | container inspect | fake | Published port bindings are derived from Apple inspect. |
| docker ps | SUPPORTED | container list | fake | Stateless filters cover id, name, label, status, ancestor, network, and volume. |
| docker pull | PARTIAL | container image pull | live | Basic form only; Docker-only flags are not translated. |
| docker push | PARTIAL | container image push | live | Basic form only; Docker-only flags are not translated. |
| docker rename | UNSUPPORTED | - | fake | No Apple equivalent; refused with exit 64. |
| docker restart | PARTIAL | - | fake | Composed from Apple stop + start; Apple has no restart. |
| docker rm | SUPPORTED | container rm | fake | Maps -f force removal to Apple container rm. |
| docker rmi | PARTIAL | container image rm | fake | Top-level alias for image rm; Docker-only flags are not translated. |
| docker run | SUPPORTED | container run | live | Unverified options are refused with exit 64 before any container is created. |
| docker save | PARTIAL | container image save | fake | Basic form only; Docker-only flags are not translated. |
| docker start | SUPPORTED | container start | fake | Non-attached starts refresh Compose /etc/hosts mappings; attach/interactive stay passthroughs. |
| docker stats | PARTIAL | container stats | fake | Go-template --format is refused; Apple accepts json\|table\|yaml\|toml. |
| docker stop | SUPPORTED | container stop | fake | Maps -s SIGNAL and -t N to Apple container stop. D1 option semantics: stop -s; see tests/option_semantics.py. |
| docker system df | PARTIAL | container system df | fake | Maps directly; Go templates and Docker's verbose mode are refused. |
| docker system events | UNSUPPORTED | - | fake | Stateful watcher with no Apple equivalent; refused with exit 64. |
| docker system info | PARTIAL | container system status | fake | Maps to docker info; only the {{.Driver}} --format template is translated. |
| docker system prune | PARTIAL | - | fake | Non-interactive; runs Apple prune, image prune, network prune, and volume prune. |
| docker tag | PARTIAL | container image tag | live | Basic form only; Docker-only flags are not translated. |
| docker version | SUPPORTED | container --version; container system status | unit | Plain output includes the Apple CLI/client-server shape and the shim version. |
| docker volume create | PARTIAL | container volume create | fake | create/ls/rm/inspect/prune forward; subcommands Apple lacks fail loudly. |
| docker volume inspect | PARTIAL | container volume inspect | fake | create/ls/rm/inspect/prune forward; subcommands Apple lacks fail loudly. |
| docker volume ls | PARTIAL | container volume list | fake | create/ls/rm/inspect/prune forward; subcommands Apple lacks fail loudly. |
| docker volume prune | PARTIAL | container volume prune | fake | create/ls/rm/inspect/prune forward; subcommands Apple lacks fail loudly. |
| docker volume rm | PARTIAL | container volume delete | fake | create/ls/rm/inspect/prune forward; subcommands Apple lacks fail loudly. |
| compose annotations | SUPPORTED | - | unit | No runtime effect; accepted and ignored as Compose does. |
| compose attach | SUPPORTED | - | unit | No runtime effect; accepted and ignored as Compose does. |
| compose blkio_config | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose build | SUPPORTED | build | fake | Translates to `container build` of the service context, tagging the service image. |
| compose build.additional_contexts | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: not forwarded to the Apple container build. |
| compose build.args | SUPPORTED | --build-arg | fake | Each entry forwards as Apple --build-arg. |
| compose build.cache_from | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: not forwarded to the Apple container build. |
| compose build.cache_to | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: not forwarded to the Apple container build. |
| compose build.context | SUPPORTED | - | fake | Context directory, defaulting to the project directory; passed as the build's positional argument. |
| compose build.dockerfile | SUPPORTED | --file | fake | Forwarded as Apple --file, resolved against the context. |
| compose build.dockerfile_inline | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: not forwarded to the Apple container build. |
| compose build.entitlements | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: not forwarded to the Apple container build. |
| compose build.extra_hosts | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: not forwarded to the Apple container build. |
| compose build.isolation | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: not forwarded to the Apple container build. |
| compose build.labels | SUPPORTED | - | unit | Forwarded to the build. |
| compose build.network | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: not forwarded to the Apple container build. |
| compose build.no_cache | SUPPORTED | - | unit | Forwarded to the build. |
| compose build.platforms | SUPPORTED | --platform | fake | Comma-joined into Apple --platform, falling back to the service platform. |
| compose build.privileged | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: not forwarded to the Apple container build. |
| compose build.provenance | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: not forwarded to the Apple container build. |
| compose build.pull | SUPPORTED | - | unit | Forwarded to the build. |
| compose build.sbom | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: not forwarded to the Apple container build. |
| compose build.secrets | SUPPORTED | - | unit | Forwarded to the build. |
| compose build.shm_size | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: not forwarded to the Apple container build. |
| compose build.ssh | SUPPORTED | - | unit | Forwarded to the build. |
| compose build.tags | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: not forwarded to the Apple container build. |
| compose build.target | SUPPORTED | - | unit | Forwarded to the build. |
| compose build.ulimits | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: not forwarded to the Apple container build. |
| compose cap_add | SUPPORTED | --cap-add | fake | Each entry forwards as Apple --cap-add. |
| compose cap_drop | SUPPORTED | --cap-drop | fake | Each entry forwards as Apple --cap-drop. |
| compose cgroup | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose cgroup_parent | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose command | SUPPORTED | - | live | Shell-lexically split and appended as positional args after the image. |
| compose configs | UNSUPPORTED | - | fake | Refused with exit 64 (REFUSED line), not waivable: Compose configs are not supported yet: Apple container 1.5.0 cannot mount a single file. |
| compose container_name | SUPPORTED | --name | fake | Used for Apple --name; defaults to <project>-<service>-1 when absent. |
| compose cpu_count | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose cpu_percent | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose cpu_period | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose cpu_quota | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose cpu_rt_period | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose cpu_rt_runtime | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose cpu_shares | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose cpus | SUPPORTED | --cpus | fake | Forwards to Apple --cpus, with fractional values normalized. |
| compose cpuset | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose credential_spec | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose depends_on | SUPPORTED | - | live | Starts dependencies first and honors the service_started, service_healthy and service_completed_successfully conditions. |
| compose depends_on required | SUPPORTED | - | fake | `required: false` keeps the dependency out of a partial `up` and turns a failed or impossible healthy gate into a notice; the default is required. |
| compose deploy | PARTIAL | --cpus | fake | Only deploy.resources.limits translates to --cpus/--memory; replicas != 1 is reported with a NOT APPLIED line. |
| compose deploy.endpoint_mode | SUPPORTED | - | unit | Accepted; only resources.limits translates. |
| compose deploy.labels | SUPPORTED | - | unit | Accepted; only resources.limits translates. |
| compose deploy.mode | SUPPORTED | - | unit | Accepted; only resources.limits translates. |
| compose deploy.placement | SUPPORTED | - | unit | Accepted; only resources.limits translates. |
| compose deploy.replicas | PARTIAL | - | unit | Accepted and reported with a NOT APPLIED line: the shim supports one container per service. |
| compose deploy.resources | SUPPORTED | - | unit | Accepted; only resources.limits translates. |
| compose deploy.restart_policy | PARTIAL | - | unit | Accepted and reported with a NOT APPLIED line: restart policies are not supported by Apple container. |
| compose deploy.rollback_config | SUPPORTED | - | unit | Accepted; only resources.limits translates. |
| compose deploy.update_config | SUPPORTED | - | unit | Accepted; only resources.limits translates. |
| compose develop | SUPPORTED | - | unit | No runtime effect; accepted and ignored as Compose does. |
| compose device_cgroup_rules | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose devices | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 documents no host device passthrough. |
| compose dns | SUPPORTED | --dns | fake | Each entry forwards as Apple --dns. |
| compose dns_opt | SUPPORTED | --dns-option | fake | Each entry forwards as Apple --dns-option. |
| compose dns_search | SUPPORTED | --dns-search | fake | Each entry forwards as Apple --dns-search. |
| compose domainname | PARTIAL | - | unit | Accepted and reported with a NOT APPLIED line: domainname is not supported by Apple container. |
| compose entrypoint | SUPPORTED | --entrypoint | fake | String form is shell-lexically split and forwarded as Apple --entrypoint, discarding the image CMD. |
| compose env_file | SUPPORTED | -e | fake | Files are read at up time and flattened into -e pairs. |
| compose environment | SUPPORTED | -e | live | Map and list forms flatten into Apple -e pairs. |
| compose expose | SUPPORTED | - | unit | No runtime effect; accepted and ignored as Compose does. |
| compose extends | SUPPORTED | - | unit | No runtime effect; accepted and ignored as Compose does. |
| compose external_links | PARTIAL | - | unit | Accepted and reported with a NOT APPLIED line: external_links are not applied. |
| compose extra_hosts | PARTIAL | - | fake | Accepted and reported with a NOT APPLIED line: extra_hosts is not translated. |
| compose gpus | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose group_add | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose healthcheck | PARTIAL | - | live | 'healthcheck' is only evaluated to gate depends_on service_healthy or 'up --wait'; there is no continuous monitoring and no restart on failure. |
| compose hostname | PARTIAL | - | fake | Accepted and reported with a NOT APPLIED line: custom hostname is not supported by Apple container. |
| compose image | SUPPORTED | - | live | Used as the run's positional image; required when no build section is present. |
| compose init | SUPPORTED | --init | fake | Forwards to Apple --init. |
| compose ipc | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose isolation | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose labels | SUPPORTED | --label | fake | Map and list forms translate to Apple --label alongside the shim's bookkeeping labels. |
| compose links | PARTIAL | - | unit | Accepted and reported with a NOT APPLIED line: links are not applied; service names resolve through the project network. |
| compose logging | PARTIAL | - | unit | Accepted and reported with a NOT APPLIED line: logging drivers are not applied. |
| compose mac_address | PARTIAL | - | unit | Accepted and reported with a NOT APPLIED line: mac_address is not applied. |
| compose mem_limit | SUPPORTED | --memory | fake | Forwards to Apple --memory. |
| compose mem_reservation | UNSUPPORTED | - | fake | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose mem_swappiness | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose memswap_limit | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose models | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose name | SUPPORTED | - | unit | No runtime effect; accepted and ignored as Compose does. |
| compose network_mode | UNSUPPORTED | - | fake | Refused with exit 64 (REFUSED line), not waivable: Apple container 1.5.0 cannot share, disable or replace the project network; the service would otherwise gain connectivity it did not request. |
| compose networks | PARTIAL | --network | live | Each declared network joins via Apple --network; per-network aliases are reported with a NOT APPLIED line. |
| compose oom_kill_disable | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose oom_score_adj | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose pid | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose pids_limit | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 documents no process-count limit. |
| compose platform | SUPPORTED | --platform | fake | Forwards to Apple --platform on run and on build (unless build.platforms is set); an image without that platform variant fails at run time. |
| compose ports | PARTIAL | -p | live | Publishes via Apple -p; bare container ports map to the identical host port, as Apple has no random-host-port mode. |
| compose post_start | PARTIAL | - | unit | Accepted and reported with a NOT APPLIED line: lifecycle hooks are not run. |
| compose pre_stop | PARTIAL | - | unit | Accepted and reported with a NOT APPLIED line: lifecycle hooks are not run. |
| compose privileged | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 has no privileged mode. |
| compose profiles | SUPPORTED | - | unit | No runtime effect; accepted and ignored as Compose does. |
| compose provider | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose pull_policy | PARTIAL | - | unit | Accepted and reported with a NOT APPLIED line: pull_policy is not applied. |
| compose read_only | SUPPORTED | --read-only | fake | Forwards to Apple --read-only. |
| compose restart | PARTIAL | - | fake | Accepted and reported with a NOT APPLIED line: restart policies are not supported by Apple container. |
| compose runtime | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose scale | PARTIAL | - | unit | Accepted and reported with a NOT APPLIED line: the shim supports one container per service. |
| compose secrets | UNSUPPORTED | - | fake | Refused with exit 64 (REFUSED line), not waivable: Compose secrets are not supported yet: Apple container 1.5.0 cannot mount a single file and the shim will not expose a secret any other way. |
| compose security_opt | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 has no seccomp/AppArmor/no-new-privileges knob. |
| compose shm_size | SUPPORTED | --shm-size | fake | Forwards to Apple --shm-size. |
| compose stdin_open | SUPPORTED | -i | fake | Forwards to Apple -i. |
| compose stop_grace_period | PARTIAL | - | unit | Accepted and reported with a NOT APPLIED line: stop_grace_period is not applied; the runtime default is used. |
| compose stop_signal | PARTIAL | - | unit | Accepted and reported with a NOT APPLIED line: stop_signal is not applied; the runtime default is used. |
| compose storage_opt | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose sysctls | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 documents no sysctl passthrough. |
| compose tmpfs | SUPPORTED | --tmpfs | fake | Each entry forwards as Apple --tmpfs. |
| compose top-level name | SUPPORTED | - | fake | Sanitized into the project name that scopes container, network and volume resources. |
| compose top-level networks | PARTIAL | network create | live | Declared networks are created at up time honoring --internal; external networks are warned and not created. |
| compose top-level networks.attachable | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line), not waivable: changes how the network behaves and has no Apple equivalent. |
| compose top-level networks.driver | SUPPORTED | - | unit | Honoured when creating the resource. |
| compose top-level networks.driver_opts | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line), not waivable: changes how the network behaves and has no Apple equivalent. |
| compose top-level networks.enable_ipv4 | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line), not waivable: changes how the network behaves and has no Apple equivalent. |
| compose top-level networks.enable_ipv6 | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line), not waivable: changes how the network behaves and has no Apple equivalent. |
| compose top-level networks.external | SUPPORTED | - | fake | Attached by its own name (`name:` or the key); never created or removed. `up` exits 1 when it does not exist, before creating anything. |
| compose top-level networks.internal | SUPPORTED | - | unit | Honoured when creating the resource. |
| compose top-level networks.ipam | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line), not waivable: changes how the network behaves and has no Apple equivalent. |
| compose top-level networks.labels | SUPPORTED | - | unit | Honoured when creating the resource. |
| compose top-level networks.name | SUPPORTED | - | fake | Used verbatim as the runtime network name, without the project prefix. |
| compose top-level services | SUPPORTED | - | fake | Each entry becomes a service, started in topological (dependency) order. |
| compose top-level volumes | PARTIAL | - | live | Parsed and shown by `config`; named volumes are created on demand from service references. |
| compose top-level volumes.driver | SUPPORTED | - | unit | Honoured when creating the resource. |
| compose top-level volumes.driver_opts | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line), not waivable: changes where the data lives and has no Apple equivalent. |
| compose top-level volumes.external | SUPPORTED | - | live | Mounted by its own name (`name:` or the key); never created, relabelled or removed. `up` exits 1 naming the volume and service when it does not exist; a leftover `<project>_<key>` copy from earlier versions only gets a notice. |
| compose top-level volumes.labels | SUPPORTED | - | unit | Honoured when creating the resource. |
| compose top-level volumes.name | SUPPORTED | - | fake | Used verbatim as the runtime volume name, without the project prefix. |
| compose tty | SUPPORTED | -t | fake | Forwards to Apple -t. |
| compose ulimits | SUPPORTED | --ulimit | fake | Map or list form translates to Apple --ulimit as type=soft[:hard]. |
| compose use_api_socket | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose user | SUPPORTED | --user | fake | Forwards to Apple --user. |
| compose userns_mode | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose uts | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose volumes | PARTIAL | -v | live | Host paths become --mount binds (ro becomes readonly, other mode flags dropped); bare names become project-scoped named volumes on -v. |
| compose volumes.Z | SUPPORTED | - | unit | Accepted in short and long volume syntax. |
| compose volumes.bind | SUPPORTED | - | unit | Accepted in short and long volume syntax. |
| compose volumes.cached | SUPPORTED | - | unit | Accepted in short and long volume syntax. |
| compose volumes.consistency | SUPPORTED | - | unit | Accepted in short and long volume syntax. |
| compose volumes.delegated | SUPPORTED | - | unit | Accepted in short and long volume syntax. |
| compose volumes.image | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line), not waivable: image mounts are not supported. |
| compose volumes.nocopy | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line), not waivable: Apple container 1.5.0 has no equivalent. |
| compose volumes.propagation | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line), not waivable: Apple container 1.5.0 has no equivalent. |
| compose volumes.read_only | SUPPORTED | - | unit | Accepted in short and long volume syntax. |
| compose volumes.ro | SUPPORTED | - | unit | Accepted in short and long volume syntax. |
| compose volumes.rw | SUPPORTED | - | unit | Accepted in short and long volume syntax. |
| compose volumes.source | SUPPORTED | - | unit | Accepted in short and long volume syntax. |
| compose volumes.subpath | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line), not waivable: Apple container 1.5.0 has no equivalent. |
| compose volumes.target | SUPPORTED | - | unit | Accepted in short and long volume syntax. |
| compose volumes.tmpfs | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line), not waivable: tmpfs mounts in `volumes` are not translated; use the service `tmpfs` key. |
| compose volumes.type | SUPPORTED | - | unit | Accepted in short and long volume syntax. |
| compose volumes.volume | SUPPORTED | - | unit | Accepted in short and long volume syntax. |
| compose volumes.z | SUPPORTED | - | unit | Accepted in short and long volume syntax. |
| compose volumes_from | UNSUPPORTED | - | unit | Refused with exit 64 (REFUSED line) unless named in CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED: Apple container 1.5.0 has no equivalent. |
| compose working_dir | SUPPORTED | -w | fake | Forwards to Apple -w. |
| compose x-shim-boot-hosts | SUPPORTED | - | fake | Shim extension: false skips the boot-time /etc/hosts entrypoint wrapper for the service. |
| docker compose ps --format json | SUPPORTED | container list --all --format json | unit | JSON-lines fields and value types follow the captured Compose 5.6.0 reference fixture; a listing that includes an exited container is refused (exit 64) because the runtime exposes no numeric exit code. |
| docker compose ps --format table | SUPPORTED | container list --all --format json | fake | Default Compose table columns are NAME, IMAGE, COMMAND, SERVICE, CREATED, STATUS, PORTS. |
| docker compose ps --format template | UNSUPPORTED | - | fake | Go-template output is refused with exit 64. |
| docker compose ps --format yaml\|toml | UNSUPPORTED | - | fake | Only json and table are supported formats; other values are refused with exit 64. |
| docker version --format json | SUPPORTED | container --version; container system status | fake | Bounded JSON object with Client.Version and Server.Version strings. |
| docker version --format version templates | SUPPORTED | container --version; container system status | fake | Accepts only {{.Client.Version}} and {{.Server.Version}}; other templates are refused with exit 64. |
| build --add-host | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 documents no add-host flag. |
| build --arch | SUPPORTED | --arch | fake | Forwarded verbatim to Apple --arch. |
| build --build-arg | SUPPORTED | --build-arg | live | Forwarded verbatim to Apple --build-arg. |
| build --build-context | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 documents no build-context flag. |
| build --cache-from | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 documents no cache-from flag. |
| build --cache-to | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 documents no cache-to flag. |
| build --cpu-shares | UNSUPPORTED | - | fake | Docker --cpu-shares sets CPU weight shares (a relative fraction of CPU time); Apple container --cpus specifies an integer count of CPUs. Refused with exit 64 and a message naming both meanings. D1 option semantics: build --cpu-shares; see tests/option_semantics.py. |
| build --cpus | SUPPORTED | --cpus | fake | Apple-only build extension (Docker build has no --cpus option); forwarded to Apple container 1.5.0 --cpus. D1 option semantics: build --cpus; see tests/option_semantics.py. |
| build --file | SUPPORTED | --file | fake | Forwarded verbatim to Apple --file. |
| build --force-rm | PARTIAL | - | fake | Docker uses --force-rm to request removal of intermediate build containers on build failure. Apple container 1.5.0 documents no corresponding flag, so the shim accepts it as a no-op instead of forwarding an unsupported option. D1 option semantics: build --force-rm; see tests/option_semantics.py. |
| build --help | SUPPORTED | --help | fake | Forwards to Apple container build --help. |
| build --iidfile | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 documents no iidfile flag. |
| build --label | SUPPORTED | --label | live | Forwarded verbatim to Apple --label. |
| build --load | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 documents no load flag. |
| build --memory | SUPPORTED | --memory | fake | Unit letter upper-cased before forwarding; sets the builder container memory. D1 option semantics: build --memory; see tests/option_semantics.py. |
| build --network | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 documents no build network flag. |
| build --no-cache | SUPPORTED | --no-cache | fake | Forwarded verbatim to Apple --no-cache. |
| build --output | SUPPORTED | --output | fake | Forwarded verbatim to Apple --output. |
| build --platform | SUPPORTED | --platform | live | Forwarded verbatim to Apple --platform, including a comma list (`linux/amd64,linux/arm64`), which Apple 1.5.0 builds into one local tag holding an index with both variants (findings 1.2). |
| build --progress | SUPPORTED | --progress | fake | Forwarded verbatim to Apple --progress. |
| build --pull | SUPPORTED | --pull | fake | Forwarded verbatim to Apple --pull. |
| build --push | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 documents no build push flag. |
| build --quiet | SUPPORTED | --quiet | fake | Forwarded verbatim to Apple --quiet. |
| build --rm | PARTIAL | - | fake | Docker uses --rm to request removal of intermediate build containers. Apple container 1.5.0 documents no corresponding flag, so the shim accepts it as a no-op instead of forwarding an unsupported option. D1 option semantics: build --rm; see tests/option_semantics.py. |
| build --secret | SUPPORTED | --secret | fake | Forwarded verbatim to Apple --secret (`id=<id>,src=<path>` or `id=<id>,env=<VAR>`, verified live in findings 1.5). |
| build --ssh | PARTIAL | --ssh default | fake | Only `--ssh default` is forwarded (the host agent, as Apple container 1.5.0 build supports); any other form is refused with exit 64. |
| build --tag | SUPPORTED | --tag | fake | Forwarded verbatim to Apple --tag. |
| build --target | SUPPORTED | --target | fake | Forwarded verbatim to Apple --target. |
| build --ulimit | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 documents no build ulimit flag. |
| build -c | UNSUPPORTED | - | fake | Docker -c sets CPU weight shares (a relative fraction of CPU time); Apple container --cpus specifies an integer count of CPUs. Refused with exit 64 and a message naming both meanings. D1 option semantics: build -c; see tests/option_semantics.py. |
| compose up --no-deps | SUPPORTED | - | fake | Creates only the named services and prints a notice listing the skipped dependencies. |
| run --add-host | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 documents no add-host flag. |
| run --arch | SUPPORTED | --arch | fake | Apple-only extension (not a Docker run option); forwarded to Apple container 1.5.0 --arch. D1 option semantics: run --arch; see tests/option_semantics.py. |
| run --cap-add | SUPPORTED | --cap-add | fake | Forwarded verbatim; the runtime normalizes names to CAP_*. D1 option semantics: run --cap-add; see tests/option_semantics.py. |
| run --cap-drop | SUPPORTED | --cap-drop | live | Forwarded verbatim; the runtime normalizes names to CAP_*. D1 option semantics: run --cap-drop; see tests/option_semantics.py. |
| run --cidfile | SUPPORTED | --cidfile | fake | Forwarded verbatim to Apple --cidfile. |
| run --cpu-shares | UNSUPPORTED | - | fake | Docker --cpu-shares sets CPU weight shares (a relative fraction of CPU time); Apple container --cpus specifies an integer count of CPUs. Refused with exit 64 and a message naming both meanings. D1 option semantics: run --cpu-shares; see tests/option_semantics.py. |
| run --cpus | SUPPORTED | --cpus | fake | Only whole (integer) CPU counts are supported; fractional values are refused with exit 64. Integral values are normalized to their whole form (2.0 -> 2) before forwarding to Apple --cpus. D1 option semantics: run --cpus; see tests/option_semantics.py. |
| run --cwd | SUPPORTED | -w | fake | Alias of --workdir; translated to Apple -w. |
| run --detach | SUPPORTED | -d | fake | Detached run re-adds -d and generates a --name when none is given; create refuses -d with exit 64. |
| run --device | UNSUPPORTED | - | fake | Refused with exit 64 as an unsupported run option; Apple container 1.5.0 documents no host device passthrough. D1 option semantics: run --device; see tests/option_semantics.py. |
| run --dns | SUPPORTED | --dns | fake | Forwarded verbatim to Apple --dns. |
| run --dns-option | SUPPORTED | --dns-option | fake | Forwarded verbatim to Apple --dns-option. |
| run --dns-search | SUPPORTED | --dns-search | fake | Forwarded verbatim to Apple --dns-search. |
| run --entrypoint | SUPPORTED | --entrypoint | fake | Forwarded verbatim to Apple --entrypoint. |
| run --env | SUPPORTED | -e | fake | Forwarded verbatim to Apple -e. |
| run --env-file | SUPPORTED | --env-file | fake | Forwarded verbatim to Apple --env-file. D1 option semantics: run --env-file; see tests/option_semantics.py. |
| run --group-add | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 has no group-add namespace/group option. D1 option semantics: run --group-add; see tests/option_semantics.py. |
| run --hostname | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 documents no hostname flag. |
| run --init | SUPPORTED | --init | fake | Forwarded verbatim to Apple --init. |
| run --interactive | SUPPORTED | -i | fake | Forwarded verbatim to Apple -i. |
| run --ipc | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 has no ipc namespace/group option. D1 option semantics: run --ipc; see tests/option_semantics.py. |
| run --label | SUPPORTED | --label | fake | Forwarded verbatim to Apple --label. |
| run --memory | SUPPORTED | --memory | fake | Unit letter upper-cased before forwarding; the runtime applies 1 MiB granularity. D1 option semantics: run --memory; see tests/option_semantics.py. |
| run --mount | SUPPORTED | --mount | fake | Parsed and validated, then translated to the Apple --mount dialect (type/source/target/readonly); unsupported or duplicate keys are refused with exit 64 rather than forwarded verbatim. D1 option semantics: run --mount; see tests/option_semantics.py. |
| run --name | SUPPORTED | --name | fake | Forwarded verbatim to Apple --name. |
| run --network | SUPPORTED | --network | fake | bridge and default map to Apple's default network (flag omitted); named networks forward verbatim; none, host and container:* are refused (see the per-mode entries). D1 option semantics: run --network; see tests/option_semantics.py. |
| run --network bridge | SUPPORTED | - | fake | Docker's bridge is its default network; the flag is omitted so Apple attaches its own default network. |
| run --network container: | UNSUPPORTED | - | fake | Docker --network=container:NAME shares another container's network namespace; Apple container 1.5.0 has no container network mode. Refused with exit 64 as an unsafe translation. |
| run --network default | SUPPORTED | - | fake | Docker's default is its default network; the flag is omitted so Apple attaches its own default network. |
| run --network host | UNSUPPORTED | - | fake | Docker --network=host shares the host network namespace; Apple container 1.5.0 has no host network. Refused with exit 64 as an unsafe translation. |
| run --network none | UNSUPPORTED | - | fake | No verified Apple container equivalent; refused with exit 64 as an unsafe translation. |
| run --no-dns | SUPPORTED | --no-dns | fake | Apple-only extension (not a Docker run option); forwarded to Apple container 1.5.0 --no-dns. D1 option semantics: run --no-dns; see tests/option_semantics.py. |
| run --pid | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 has no pid namespace/group option. D1 option semantics: run --pid; see tests/option_semantics.py. |
| run --pids-limit | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 documents no process-count limit. D1 option semantics: run --pids-limit; see tests/option_semantics.py. |
| run --platform | SUPPORTED | --platform | live | Forwarded verbatim to Apple --platform. |
| run --privileged | UNSUPPORTED | - | live | Refused with exit 64; Apple container 1.5.0 has no privileged mode. D1 option semantics: run --privileged; see tests/option_semantics.py. |
| run --publish | SUPPORTED | --publish | fake | Forwarded verbatim to Apple --publish. |
| run --read-only | SUPPORTED | --read-only | live | Forwarded verbatim; guest writes fail on the read-only root filesystem. |
| run --rm | SUPPORTED | --rm | fake | Forwarded verbatim to Apple --rm. |
| run --runtime | UNSUPPORTED | - | fake | Docker --runtime selects an OCI runtime (runc); Apple container --runtime selects a container runtime handler. Refused with exit 64 and a message naming both meanings. D1 option semantics: run --runtime; see tests/option_semantics.py. |
| run --security-opt | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 documents no seccomp, AppArmor, or no-new-privileges knob. D1 option semantics: run --security-opt; see tests/option_semantics.py. |
| run --shm-size | SUPPORTED | --shm-size | fake | Forwarded verbatim to Apple --shm-size. |
| run --storage-opt | UNSUPPORTED | - | fake | Refused with exit 64; Apple container 1.5.0 documents no storage driver options, and the create probe exits 125. |
| run --sysctl | UNSUPPORTED | - | fake | Refused with exit 64 as an unsupported run option; Apple container 1.5.0 documents no sysctl passthrough. |
| run --tmpfs | SUPPORTED | --tmpfs | live | Forwarded verbatim including options, which the runtime applies to the mount. |
| run --tty | SUPPORTED | -t | fake | Forwarded verbatim to Apple -t. |
| run --ulimit | SUPPORTED | --ulimit | fake | Forwarded verbatim to Apple --ulimit. |
| run --user | SUPPORTED | --user | live | Forwarded verbatim; a numeric user maps to that UID in the guest. D1 option semantics: run --user; see tests/option_semantics.py. |
| run --volume | PARTIAL | --mount | fake | Bind mounts translate to Apple --mount with ro becoming readonly; Docker SELinux relabel modes z/Z are refused with exit 64 because the supported Apple macOS runtime cannot represent them, and other unknown mode flags are refused too. D1 option semantics: run --volume; see tests/option_semantics.py. |
| run --workdir | SUPPORTED | -w | fake | Translated to Apple -w, accepting the --cwd and -w aliases. |
| run -c | UNSUPPORTED | - | fake | Docker -c sets CPU weight shares (a relative fraction of CPU time); Apple container -c is a combined form of --cpus specifying an integer count of CPUs. Refused with exit 64 and a message naming both meanings. D1 option semantics: run -c; see tests/option_semantics.py. |
<!-- compat-matrix:end -->

### Building and publishing for AWS

Apple silicon builds `linux/arm64` natively. What reaches the registry is
decided by the build and push, not by the machine you run it on:

- **Single manifest versus index.** `docker push` (Apple `container image push`)
  always publishes an OCI image *index*, even for a single-platform build: an
  arm64-only build is an index with one `arm64` entry. ECR, ECS and Fargate
  accept indexes, and the task definition's platform has to match one of the
  entries.
- **Build for the platform you deploy.** `docker build --platform
  linux/amd64 -t app .` produces an amd64 image on an arm64 Mac (under
  emulation), and `--platform linux/amd64,linux/arm64` produces one tag holding
  both variants.
- **ECR login.** `aws ecr get-login-password --region <region> | docker login
  --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com`.
  Apple `container` stores the credential in the macOS login keychain; the shim
  writes no file and does not touch `~/.docker/config.json`. The live tests
  prove login against a disposable local registry, not ECR token
  authentication.
- **Fargate/ECS.** Set the task definition `runtimePlatform` to the
  architecture you built (`ARM64` or `X86_64`). A Fargate service defaults to
  `X86_64`, so an arm64-only image fails to start there unless the task
  definition says `ARM64`.
- **A local ARM64 run does not validate AMD64.** Running the arm64 variant on
  your Mac proves nothing about the amd64 variant you ship to an x86_64
  Fargate service. To exercise the amd64 image locally, pass the platform
  explicitly (`docker run --platform linux/amd64 ...`); an image that only has
  an amd64 variant fails to run without it, where Docker Desktop would pick the
  image's platform with a warning.

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

Every test belongs to exactly one of four tiers, declared by a `TIER` constant
in its module:

| Tier | Runs by default | Proves |
| --- | --- | --- |
| `unit` | yes | pure translation and parsing logic |
| `fake` | yes | the generated `container` command, against a fake binary |
| `live` | no (`SHIM_TIERS=live`) | behavior on the supported Apple `container` release |
| `conformance` | no (`SHIM_TIERS=conformance`) | the same fixtures on a real Docker Engine |

```bash
python3 -m unittest discover -s tests -v
```

The default run starts no container; live and conformance tests are reported as
skipped with the reason (missing runtime, wrong release, no Docker client).
Live tests use the disposable `shimtest-` prefix, clean up in every outcome and
never run a global prune. They assert observable behavior (connections,
persisted data, name resolution, exit codes, cleanup, refusal of unsupported
options), not only a zero exit status. Compose fixtures A to L in
`tests/fixtures/compat/` use pinned images and are shared unmodified by both
opt-in tiers.

A passing fake-runtime test proves only that a command was translated
correctly, a live test proves it works on Apple `container`, and only a
conformance test says anything about Docker Engine. A report never claims a
test passed unless it ran: a tier that did not run is "not run".

To evaluate a candidate Apple `container` release, run
`python3 scripts/validate-runtime.py`. It runs the read-only parse checks per
matrix entry and the live tier, reports which evidence still holds and never
edits `SUPPORTED_CONTAINER_VERSION`. See [docs/testing.md](docs/testing.md) for
the disposable-name rules, the fixtures, the CI recipe and the steps for
advancing the supported runtime. The current development and test-fixture
baseline is the supported release stated in [Requirements](#requirements).
