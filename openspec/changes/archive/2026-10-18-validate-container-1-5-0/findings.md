# Findings

Runtime under test: Apple `container` 1.5.0 (Homebrew), shim run with
`CONTAINER_DOCKER_SHIM_CONTAINER=/opt/homebrew/bin/container` and the supported
version constant still at 1.4.1.

## 1.1 Runtime

- `container system version`: `container` and `container-apiserver` both 1.5.0, build `release`.
- `container system status`: `status running`, `client.version 1.5.0`.

## 1.2 Empty-runtime matrix

| Command | Result |
| --- | --- |
| `docker version` | exit 0, reports container CLI 1.5.0 |
| `docker info` | exit 0 |
| `docker ps -a` | exit 0, header only (no containers exist) |
| `docker images` / `image ls` | exit 0, but wrong SIZE and CREATED values (see 1.3) |
| `docker image inspect docker.io/library/postgres:17` | exit 0, `Size` 159971863, `Created` 2026-09-19T00:38:59.786253347Z |

No strict-parse error was raised by any command: the 1.5.0 payloads for image
list and image inspect satisfy the parsers unchanged. The shim does not read
the runtime version for gating; only JSON shape is enforced.

Commands that need a container (inspect, port, logs, compose ps) are deferred
to 4.2, because no container exists to query.

Before task 2.2 the shim also failed with `Apple container CLI not found:
/usr/local/bin/container` on this machine, because the default path is fixed
and Homebrew installs to `/opt/homebrew/bin`. Fixed by 2.2a.

## 1.3 JSON shape and `docker images` defect

`container image ls --format json` item keys: `configuration`
(`creationDate`, `descriptor` {`digest`, `mediaType`, `size`}, `name`), `id`,
`variants` (each with `config`, `digest`, `platform`, `size`). All keys the
parsers read are present; no historical field names are needed.

Two defects in the image list row builder, both caused by which field it reads:

1. **Size.** The row uses `configuration.descriptor.size`, the byte size of the
   OCI index manifest. For `postgres:17` that is 10237 (shown as 10.2kB) while
   the arm64 variant `size` is 159971863. `image inspect` already prefers the
   variant size, so the two commands disagree.
2. **Creation time.** The row uses `configuration.creationDate`. For
   `ghcr.io/apple/containerization/vminit:0.45.0` the runtime reports
   `1970-01-01T00:00:00Z` (no variant `config.created` either), which the shim
   renders as "56 years ago". `image inspect` prefers the variant
   `config.created`.

Both behaviours come from the shim's own field choice, not from a payload
change, so they are not 1.5.0 drift. Reproducing on 1.4.1 was not attempted:
the apiserver on this machine is 1.5.0 and the logic does not depend on the
runtime version.

Container list and inspect payloads cannot be captured until a container
exists; their key sets are compared in 4.2.

## 4.x Live validation after the fixes

Run with `CONTAINER_DOCKER_SHIM_CONTAINER` unset (the shim found
`/opt/homebrew/bin/container` through `PATH`) and the supported-version
constant at 1.5.0.

- Empty-runtime matrix (version, info, ps -a, images, image inspect): all exit 0,
  no stderr. `docker images` now shows `postgres:17` at 160MB and the `vminit`
  image with `N/A` as creation time.
- Lifecycle on one disposable container, `dfac-validate-150`
  (`postgres:17`, port published on `127.0.0.1:18432`): `run -d`, `ps -a`,
  `inspect .State.Status` (`running`), `port` (`5432/tcp -> 127.0.0.1:18432`),
  `logs --tail 3`, `exec pg_isready` (accepting connections), `compose ps`
  (empty, as expected), `stop`, `inspect` (`exited`), `rm`. Afterwards
  `container ls -a` was empty and no shim-owned state existed.
- `container ls -a --format json` payload keys: `configuration`, `id`,
  `status`. Every field path the parsers read (`configuration.creationDate`,
  `.image`, `.initProcess`, `.labels`, `.mounts`, `.publishedPorts`,
  `.resources`, `status.networks`, `.startedDate`, `.state`) is present, so
  there is no 1.5.0 schema drift in container list or inspect either.

## Conclusion

1.5.0 introduced no JSON or CLI changes that the shim needed to follow. The two
real defects found were independent of the runtime version: the fixed
`/usr/local/bin/container` default, and the image list reading the index
manifest size and an epoch creation date. Both are fixed and covered by tests.
