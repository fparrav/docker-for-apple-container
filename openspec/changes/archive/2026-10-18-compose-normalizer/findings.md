# Findings (Docker Compose 5.6.0, macOS 26, arm64)

## 1.1 `config` without a Docker Engine

`docker-compose` 5.6.0 is installed at `/opt/homebrew/bin/docker-compose`. No Engine
is reachable on this machine (`/var/run/docker.sock` is a dangling symlink to
`~/.docker/run/docker.sock`, which does not exist) and every call below also set
`DOCKER_HOST=unix:///nonexistent.sock`. All of them exit 0 and need no daemon:

- `docker-compose config --format json` on the fixture in `tests/fixtures/compose/full/`
  (a YAML anchor with a `<<` merge key, a `|` block scalar `command`, `${VAR:-default}`
  with a `.env`, an `env_file`, a profiled service, short `ports` and `volumes`).
- `docker-compose config -q`.
- `-f compose.yaml -f compose.override.yaml config --format json` (the override wins:
  `EXTRA` is added to `environment`, a third port appears).
- `--profile debug config --format json` (adds the `debug` service; without the flag
  the service is absent).
- `--project-directory D -f D/compose.yaml -p mynorm --env-file D/.env` run from another
  directory (name is `mynorm`, build context is absolute under `D`).
- `COMPOSE_FILE=a:b` and `COMPOSE_PROFILES=debug` in the environment are honoured by the
  binary itself, so the shim only has to pass its environment through.
- `docker-compose version --short` prints `5.6.0`.

Failure behaviour the adapter must cover: invalid YAML exits 1 with the message on stderr
(`go-yaml load error in parser ...`); a schema violation (unknown service key) exits 1 with
`validating <file>: ...` on stderr (`config -q` also exits 1, while the JSON form still
prints the document and exits 0); an unset variable is only a `level=warning` line on
stderr; no compose file in the directory exits 1 with `no configuration file provided`.

## 1.2 Shape of `config --format json`

Captured output lives in `tests/fixtures/compose/normalized/` (see the README there).
Differences from what `compose.py` reads today:

- `ports`: always a list of objects `{mode, target, published (string), protocol,
  host_ip?}`. A port without a host side has no `published`. The built-in code expects
  strings, so the adapter renders `[host_ip:]published:target[/proto]` (or just `target`).
- `volumes` (service): list of objects `{type: volume|bind, source, target, read_only?}`.
  `compose.py` already accepts the object form. Bind `source` is absolute. Named-volume
  `source` is the short key (`data`), not the project-prefixed name.
- `volumes` (top level): `{data: {name: "<project>_data"}}`; `name` is always present.
- `networks`: service `networks: {default: null}`; top level has `default` with
  `name: "<project>_default"` even when the file declares none.
- `environment`: always a mapping, with `env_file` contents and `.env` interpolation
  already merged; the `env_file` key is gone. Values are strings.
- `command` / `entrypoint`: lists, or `null` when unset. A `|` scalar becomes
  `["sh", "-c", "echo starting\nsleep infinity"]`; a plain string command is split.
- `depends_on`: always the mapping form `{svc: {condition, required}}`.
- `healthcheck`: durations stay strings (`"1m30s"`, `"5s"`); `test` is a list.
- `build`: object with absolute `context`, `dockerfile` (default `Dockerfile`) and `args`.
- `name`: the resolved project name (`-p`, `COMPOSE_PROJECT_NAME`, `name:` or the
  directory name).
- `x-*` top-level extension fields are kept and must be ignored.
- Paths are absolute; the file never contains the original anchors or aliases.

## 1.3 Decision

`config` works without an Engine for every input tried, so the adapter proceeds. The
tested release is Docker Compose 5.6.0.

## Decisions recorded during implementation

- Explicit `-f` paths are resolved relative to the current directory, like Compose; files are forwarded to the official binary only when the user gave `-f`/`COMPOSE_FILE`, so Compose's automatic `compose.override.yaml` merge is preserved.
- The built-in source refuses an automatically merged override file instead of ignoring it.
- The config hash differs between sources (the official model normalizes more fields), so switching normalizer may recreate containers once. Documented in the README.
- The subset check also covers flow-collection anchors/aliases and content after a leading `---` on the marker line (Codex R-n31, R-n33).
- Codex R-n46: the real-binary equivalence test now requires the exact version `5.6.0`.
- Final checks: `python3 -m unittest discover -s tests -v` → 411 tests OK (2 opt-in live tests skipped); `openspec validate compose-normalizer --strict` valid.
