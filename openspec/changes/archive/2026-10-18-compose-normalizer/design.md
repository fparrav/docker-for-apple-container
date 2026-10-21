# Design

## Context

`compose.py` loads a file with `parse_yaml`, interpolates `${VAR}` with its own
resolver, and builds `Project`/`Service` objects around the resulting dict.
Everything downstream (`_build_run_args`, `cmd_up`, `topo_sorted`, volume and
network helpers) reads that dict. The official `docker-compose` 5.6.0 is
available as a Homebrew bottle and is a standalone executable. Its `config`
subcommand renders the merged model and is documented to work without
contacting the Docker Engine, but this has not yet been verified on this
machine (task 1.1).

## Goals / Non-Goals

**Goals:**
- Compose semantics come from Compose, not from a second implementation.
- The runtime-facing interface is one dictionary.
- Behaviour without the official binary is explicit and safe.

**Non-Goals:**
- Making the official binary mandatory. The shim must still start with no
  extra install.
- Executing Compose through the official binary (`docker-compose up`) with a
  fake Docker socket.

## Decisions

**Shell out to `config --format json`, parse the JSON.** The adapter runs
`docker-compose [global flags] config --format json` and loads the output with
`json.loads`. Global flags (`-f`, `--env-file`, `--profile`, `-p`,
`--project-directory`) are forwarded verbatim, so semantics stay Compose's.
Alternatives: (a) replace the parser with a vendored YAML library, rejected
because it adds a dependency and still leaves merging, interpolation and
profiles to re-implement; (b) call `docker compose config` through the shim
itself, rejected as a loop.

**Two sources behind one function.** `load_model(args) -> dict` returns the
normalized dict from whichever source is active. The current parser stays as
the second source. The orchestration functions are not changed, which keeps the
diff small and the existing 125 tests as a safety net.

**Mode selection is explicit and visible.** If `CONTAINER_DOCKER_SHIM_COMPOSE`
is set, it must work or the command fails (no silent fallback after an explicit
choice). Otherwise `docker-compose` on `PATH` is used if present. The result is
printed by `docker compose version` as `normalizer: official 5.6.0` or
`normalizer: built-in subset`. Alternative: always fall back silently; rejected
because the behaviour would change with the machine state.

**Loop guard.** The adapter resolves the real path of the chosen executable and
refuses it if it equals the shim's launcher or if the executable's environment
carries a marker variable the shim sets before spawning. The marker is also what
stops a `docker-compose` plugin wrapper from re-entering the shim.

**Built-in parser refuses, not misreads.** Add detection of anchors (`&`, `*`),
merge keys (`<<`), `|`/`>` scalars, multi-document markers, and top-level
`include`/`extends`; any of them exits 64 with the install hint. Multiple `-f`
files exit 64 as well.

**Normalized model differences to handle.** `config --format json` resolves
paths to absolute, expands short syntax of `ports` and `volumes` into objects,
and renders durations and sizes in its own form. The adapter converts only what
the orchestrator reads, and a unit test pins each conversion using captured
output (task 1.2), not hand-written fixtures.

## Risks / Trade-offs

- [`config` needs the Engine for some inputs] → Task 1.1 decides; if it does,
  the adapter is dropped and the built-in parser's limits are documented as
  final.
- [JSON shape changes between Compose releases] → The adapter validates the
  fields it reads and fails with a message naming the Compose version; the
  tested version is documented.
- [Two sources can disagree] → The equivalence test compares the runtime
  command sequence for a shared fixture.
- [Official binary prints warnings on stderr] → They are passed through, since
  they usually concern the user's file.
