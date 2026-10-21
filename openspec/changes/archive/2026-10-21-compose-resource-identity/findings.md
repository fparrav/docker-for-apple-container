# Findings: compose-resource-identity integration run

Run on 2026-10-19, macOS 26, Apple `container` CLI 1.5.0 (the supported release),
official `docker-compose` normalizer installed. Tree: uncommitted, on top of the
uncommitted `compose-capability-gate` work.

## Commands and results

| Command | Result |
| --- | --- |
| `python3 -m unittest discover -s tests` | 737 tests, OK, 45 skipped (tiers not enabled) |
| `python3 scripts/validate-runtime.py --container /opt/homebrew/bin/container` | live tier ran; 306 matrix entries, 0 problems |
| `SHIM_TIERS=live CONTAINER_DOCKER_SHIM_CONTAINER=/opt/homebrew/bin/container python3 -m unittest -v tests.test_live_fixtures` | 17 tests, OK (fixtures A to L, M, N, O, P), 152 s |
| `python3 scripts/render_compat_table.py --check README.md` | exit 0 |
| `openspec validate compose-resource-identity --strict` | run at the end of the change |

After the live runs no `shimtest-` container, volume or network remained.

## Tiers that did not run

- **conformance**: not run. No real Docker Engine client is configured on this
  machine (`SHIM_CONFORMANCE_DOCKER` unset). The conformance variants of
  fixtures O and P are written and import cleanly; they report skipped with a
  reason. Their Docker-side assertions (including the `Health.Log` comparison in
  fixture P) have never executed.

## Evidence moved

- `compose top-level volumes.external` now cites the live test of fixture O.
- `compose top-level volumes.name`, `networks.external`, `networks.name`,
  `compose depends_on required` and `compose up --no-deps` are `fake` evidence
  (fake-runtime end-to-end tests); fixture P exercises `--no-deps` live but the
  matrix entry was not re-pointed at it.

## Decisions and deviations

- The existence pass uses `container volume list` / `network list` once per
  family instead of one `inspect` per resource. Both are read-only; a list
  that fails or cannot be parsed aborts before creation, which is what
  distinguishes "missing" from "runtime error".
- `down -v` is refused (exit 64) whenever no Compose model is available,
  including when services are named, and also when the file exists but fails to
  load. A plain `down` still falls back to labels.
- A dependency on a service of an inactive profile is rejected by the official
  normalizer as "undefined service"; the shim re-reads the project with all
  profiles enabled only to name the profile that enables it. The built-in
  parser has no profile support, so it never reaches this path.
- Any dependency on an undefined service exits 64 in `plan`, whether it is
  optional or required, and also with `--no-deps`.

## Review notes

- Tasks 1.2 and 1.3: Codex found two real gaps (optional undefined dependency
  ignored; `--no-deps` skipped the undefined-dependency check). Both are fixed
  with tests, but the second fix arrived after the second and last review cycle
  and was not re-reviewed by Codex.
- Task 2.2: the "new tests fail on the previous implementation" check by
  stashing 2.1 was not done, because the tree carries other uncommitted work. The
  old tests asserted a `demo_shared` copy that the new code can no longer create,
  so they fail by construction.

## Unresolved

- Observation, not investigated: with the built-in parser, a nested mapping
  written on one line (`data: external: true`) was accepted silently and the
  volume was treated as managed. The official normalizer, which is used whenever
  it is installed, rejects it. Worth a decision on whether the built-in subset
  checker should refuse it.
- Conformance evidence for fixtures O and P is still missing (see above).
