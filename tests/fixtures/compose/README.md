# Compose fixtures

`full/` is a project that exercises the constructs the official normalizer
resolves: a YAML anchor and merge key, a `|` block scalar, `${VAR:-default}`
interpolation with a `.env` file (stored as `dot-env`), an `env_file`, a
second `-f` override file, a `debug` profile, short-syntax `ports` and
`volumes`, a `healthcheck` with Go-style durations, `depends_on` conditions and
a `build` section.

`normalized/*.json` is the verbatim output of `docker-compose config --format
json` (Docker Compose 5.6.0, no Docker Engine reachable) for that project:

- `full-default.json`: `docker-compose config` run from the project directory
  (the override file is picked up automatically).
- `full-two-files.json`: `-f compose.yaml -f compose.override.yaml`.
- `full-profile-debug.json`: `--profile debug`.

Two substitutions were applied so the files do not depend on where they were
captured: the absolute project directory is the literal `__PROJECT_DIR__`, and
the project name and resource prefixes are `full` instead of the directory name.
Tests must replace `__PROJECT_DIR__` with the real directory before use.
