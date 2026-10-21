# Findings

Live spikes on this machine: macOS 26, Apple Silicon (arm64), Apple `container` 1.5.0.
All objects were disposable (`shimtest-*`), removed by exact name afterwards.

## 1.1 Build and run per platform

Dockerfile: `FROM alpine:3.20`, `RUN uname -m > /arch.txt`, `CMD ["uname","-m"]`.

| Build | Result | Time | `cat /arch.txt` (build-time) | Run result |
| --- | --- | --- | --- | --- |
| `container build --platform linux/arm64` | OK | ~18 s (first, includes base pull) | `aarch64` | `aarch64` (~1 s) |
| `container build --platform linux/amd64` | OK | ~2 s | `x86_64` | `x86_64` only with `container run --platform linux/amd64` (~1 s) |

- The amd64 image runs under emulation without `--rosetta` being passed.
- `container run <amd64-only image>` without `--platform` fails with `Error: platform linux/arm64` (the host platform is requested and the image has no such variant). `docker run` on Docker Desktop would pick the image's platform with a warning, so this is a difference to document.
- A cached build is keyed per platform; `--no-cache` amd64 build also took ~2 s.

## 1.2 Multi-platform build

`container build --platform linux/amd64,linux/arm64 -t T .` exits 0 in ~1 s and produces **one local tag holding an index (manifest list) with both variants** (`arm64` and `amd64`).

- `container run T` picks arm64 (`aarch64`); `--platform linux/amd64` prints `x86_64`; `--platform linux/arm64` prints `aarch64`.
- The shim already passes `--platform a,b` through (`bin/docker build --platform linux/amd64,linux/arm64` exits 0). Decision for 5.2: pass multi-platform lists through (no refusal needed).

## 1.3 Push to a registry

A disposable `registry:2` container (`shimtest-registry`, `-p 127.0.0.1:5055:5000`) was used; push needs `--scheme http` for a plain-HTTP registry (`container image push --scheme http localhost:5055/shimtest/spike:TAG`). Tagging is `container image tag` (there is no top-level `container tag`).

| Pushed tag | Manifest read from the registry | Digest |
| --- | --- | --- |
| multi-platform build | `application/vnd.oci.image.index.v1+json`, manifests `[arm64, amd64]` | equals the local image digest `68259da4...` |
| arm64-only build | `application/vnd.oci.image.index.v1+json` with **a single `arm64` entry** | equals the local image digest `57922d63...` |

- Pull back (`container image pull --scheme http`) after removing the local tag yields the same digest.
- Every push is an OCI **index**, even for one platform. ECS/Fargate and ECR accept indexes; the task definition platform must match an entry.
- The registry container was stopped and removed; `container ls -a` afterwards shows only Apple's `buildkit` builder (started by the first `container build`; it was not running before).

## 1.4 Registry login

`printf pw | container registry login --scheme http -u AWS --password-stdin localhost:5055` prints `Login succeeded`.

- The credential is stored by Apple `container` in the **macOS login keychain** as an internet-password item (server `localhost:5055`, account `AWS`). No `~/.docker/config.json` change and no file written by the shim.
- `container registry logout localhost:5055` removes the item (verified with `security find-internet-password`).
- The test registry accepts any credential, so this does not prove ECR token authentication; ECR login is documented, not live-tested.

## 1.5 Build secrets and SSH

`container build --help` documents `--secret <id=key,...>` (format `id=<key>[,env=<ENV_VAR>|,src=<local/path>]`) and `--ssh <default>`.

- `--secret id=tok,src=<abs or relative path>`: OK; the build step reads the secret at `/run/secrets/tok` (12 bytes).
- `--secret id=tok,env=VAR`: OK (6 bytes with `VAR=abcdef`, verified with `--no-cache`; a cached layer hides the change because secrets are not part of the cache key).
- Missing secret: the `RUN --mount=type=secret` step fails (exit 1), no silent empty secret.
- `--ssh default`: OK; `RUN --mount=type=ssh` sees an agent socket (`sock-present`).
- The formats match Docker's `--secret id=...,src=...|env=...` and `--ssh default`. Decision for 5.1: keep `--secret` (already forwarded verbatim by `cmd_build`), and support `--ssh default` exactly (any other `--ssh` form, such as `--ssh id=path`, stays refused).

## Decisions made while atomizing

- New test modules use `tests/test_live_*.py` and the helpers `tests/tiers.py` and `tests/compat_matrix.py` (no `tests/__init__.py` exists); this replaces the `tests/live/` layout of the design.
- The conformance tier needs `SHIM_CONFORMANCE_DOCKER` pointing to a real Docker Engine client, because `docker` on this machine is the shim.

## 6.2 Wrapper overhead (10 runs each, idle runtime, no containers)

Measured on this machine with `CONTAINER_DOCKER_SHIM_CONTAINER=/opt/homebrew/bin/container`. Wall-clock per command, then the peak resident set of the largest process in the tree (`/usr/bin/time -l`).

| Command | Median | Max | Peak RSS |
| --- | --- | --- | --- |
| `container ls -a` | 18 ms | 18 ms | 29.0 MiB |
| `docker ps -a` (shim) | 65 ms | 67 ms | 29.1 MiB |
| `container --version` | 16 ms | 16 ms | 27.5 MiB |
| `docker version` (shim) | 86 ms | 92 ms | 30.5 MiB |

- The shim adds roughly 45-70 ms per command (Python start-up plus the extra `container` calls that `ps` and `version` make). The shim's own memory is below the 29-30 MiB shown, because the figure is the largest process in the tree and the `container` client alone peaks at ~27-29 MiB.
- No optimization was made: the cost is small next to container start (~1 s).

## Observations while verifying fixture G

- Compose builds ignored the service `platform` and `build.platforms`, so an amd64 service was built for the host architecture and then failed at run time. Fixed in this change (task 3.4c): both are forwarded to `container build --platform`.
- Official Compose rejects `build.platform` (singular); the valid key is `build.platforms`.
- A foreground `docker compose up` of a service whose process exits immediately did not return within 300 s in this run (the container had exited and its output was printed). Live tests must use `up -d` and then inspect state; to be checked again when writing the fixture K test (3.5c).

## Observation (3.5b): postgres on a named volume needs PGDATA in a subdirectory

An Apple named volume is an ext4 mount that contains `lost+found`. The official
`postgres:17` image fails `initdb` ("exists but is not empty") when the volume is
mounted directly at `/var/lib/postgresql/data`. Fixtures B, C and D therefore set
`PGDATA: /var/lib/postgresql/data/pgdata`, as the existing live compose test does.
This is a real difference from Docker Engine worth noting in the matrix notes.

## Observations (3.5c)

- `container inspect` on an exited container exposes no exit code (only
  `status.state: stopped`), so `docker inspect --format '{{.State.ExitCode}}'` is
  refused by the shim with exit 64 and `compose ps -a` reports the state `exited`.
  Foreground `container run` does propagate the exit code (verified: exit 3 -> rc 3).
  Fixture K's live test asserts this honest behaviour instead of an invented exit code.
- The earlier foreground `compose up` hang for a short-lived service does not
  reproduce: `up` of fixture K returns promptly.
- `restart: "no"` still prints "'restart' ignored" noise; harmless (policy is the default).

## Observation (3.6a): alpine:3.20 has no `httpd` applet

The `alpine:3.20` busybox build ships `nc` but not `httpd`; `getent` is a separate
binary (`/usr/bin/getent`). The API stand-in in fixtures B, C and D therefore serves
HTTP with a `nc -l -p 8000` loop instead of `httpd`.

## Observations (3.6b/3.6c)

- **Bug fixed in the normalizer path.** `docker-compose config --format json` re-escapes
  every literal `$` as `$$` (the output is meant to be re-read by Compose). The shim
  consumes the model directly, so `command: echo $$GREETING` reached the container as
  `$$GREETING` (the shell printed its PID). `compose_normalizer.convert_model` now undoes
  the escape in every service string (`_unescape_dollars`, covered in
  `tests/test_compose_normalizer.py`). Found by fixture H's live test.
- Service-name resolution is implemented by the shim writing `/etc/hosts` inside compose
  containers, only for peers on a shared network. A container started with plain
  `docker run --network <project>_back` does not resolve service names (no embedded DNS),
  a difference from Docker Engine. Fixture E gained `edge` (front only) and `store`
  (back only) services so isolation is tested between compose services.
- `docker compose logs --no-color` is refused (exit 64): option not supported.

## Observation (5.1): `docker build --opt=value` looped forever

`cmd_build` read an inline `--opt=value` but never advanced its argument index, so any
`docker build --tag=x .` (and every other inline value form) spun in an endless loop.
Every value option in `cmd_build` now goes through `_take_value`, which handles the
inline, attached-short and separate forms and advances the index; regression test
`test_build_accepts_inline_values_for_every_value_option`. Found while adding the
`--ssh=default` tests.

## 7.2 Live tier results (this machine, Apple `container` 1.5.0, macOS 26, arm64)

- Default run: `python3 -m unittest discover -s tests` -> 558 tests, OK, 35 skipped
  (25 live + 10 conformance, all reported as skipped with a reason).
- `scripts/validate-runtime.py --container /opt/homebrew/bin/container` (read-only
  help check plus `SHIM_TIERS=live`): 192 matrix entries, 0 problems; 30 entries
  with `live` evidence passed.
- `SHIM_TIERS=live` over `test_live_*.py` (25 tests: security 5, compose 4, fixtures
  A-L 12, images 4): passed in 3 of 4 full runs. One run immediately after the
  validator reported 1 failure that I could not attribute (its output was not
  captured) and did not reproduce in the next three full runs; treat it as an
  unexplained intermittent failure, not as a pass. No `shimtest-` containers,
  volumes or networks remained afterwards (the Apple `buildkit` builder is still
  running; the `registry:2`, `nginx:1.27-alpine` and `redis:7-alpine` images were
  pulled for these tests).
- Conformance tier: not run on this Mac (no real Docker Engine client; `docker` here is
  the shim), but run partially against a Raspberry Pi 3B (aarch64, Docker 29.5.3,
  Compose 5.1.4, Swarm active with the owner's own stack) through a throwaway wrapper that
  rsyncs the working directory to the Pi and runs `docker` over SSH (not in the repo).
  Only the light alpine-only fixtures ran: E (2 tests), H (2), I (1), K (1) -> 6/6 OK.
  Fixture A was skipped (port 8080 is taken by Traefik on the Pi) and B, C, D were skipped
  (`postgres:17` on ~370 MB of free RAM), so the 4 conformance tests for fixtures A, B, C and D
  remain unverified against a real Docker. All Pi objects were `shimtest-conf-*`, removed afterwards; the owner's services
  were left untouched (only `alpine:3.20` stays pulled).
