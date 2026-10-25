## ADDED Requirements

### Requirement: The release wheel is installed and exercised on macOS
Antes de publicar un release, la wheel adjunta SHALL instalarse y ejercitarse en un runner de macOS con `scripts/verify_wheel.py`, sobre el artefacto que construyó el job `package` y sin requerir Docker Desktop ni el CLI de Apple `container`. El job `wheel` de `ci.yml` SHALL ejecutar la misma comprobación en macOS. Estas comprobaciones SHALL permanecer separadas de la suite en vivo contra Apple `container`.

#### Scenario: Release smoke on macOS
- **WHEN** `release.yml` se inspecciona
- **THEN** la matriz de `smoke` incluye una pata de macOS que descarga el artefacto `dist` subido por `package` y corre `verify_wheel.py --dist dist --expect-version` con la versión del tag

#### Scenario: A macOS failure blocks the release
- **WHEN** la wheel falla la verificación en la pata de macOS
- **THEN** el job `release` no corre y no se publica nada

#### Scenario: CI exercises macOS before a tag
- **WHEN** `ci.yml` se inspecciona
- **THEN** el job `wheel` incluye una pata de macOS que construye la wheel y corre `verify_wheel.py --dist dist`

#### Scenario: No runtime and no Docker Desktop
- **WHEN** la pata de macOS corre en un runner alojado
- **THEN** no usa el CLI de Apple `container` real ni Docker Desktop, y `docker version` usa el reemplazo de `verify_wheel.py`

#### Scenario: Runtime absent on macOS
- **WHEN** el ejecutable `docker` instalado corre en macOS sin un CLI de Apple `container` en el PATH
- **THEN** termina con estado 1 y el mensaje dice que no se encontró el CLI y cómo indicarlo

#### Scenario: Separate from the live suite
- **WHEN** la pata de macOS termina
- **THEN** no se escribe nada en `docs/evidence/`, no se cuenta en los totales `live` y la suite en vivo no depende de ella

## MODIFIED Requirements

### Requirement: The release publishes only the artifact every smoke leg installed
In `release.yml`, one job SHALL build the distributions, one or more `smoke` legs
SHALL download that uploaded artifact and run `scripts/verify_wheel.py` on it with
`--expect-version` set to the tag's version, covering every supported Python version
on Linux and at least one macOS runner, and the job that creates the GitHub Release
SHALL depend on every `smoke` leg, SHALL download the same artifact and SHALL NOT
build again. No `smoke` leg or dependent job SHALL be allowed to fail silently
(`continue-on-error`) or to be skipped by a condition.

#### Scenario: Build once
- **WHEN** `release.yml` is inspected
- **THEN** exactly one job runs `python -m build`, and the artifact is uploaded under one name that the `smoke` and `release` jobs download

#### Scenario: A smoke leg fails
- **WHEN** the wheel fails verification on any leg of the matrix, including the macOS leg
- **THEN** the `release` job does not run and nothing is published

#### Scenario: The floor runner cannot provide the interpreter
- **WHEN** `actions/setup-python` cannot provide the floor version on the pinned runner image
- **THEN** that leg fails at its setup step, the `release` job does not run and nothing is published

#### Scenario: No bypass
- **WHEN** the `smoke` or `release` jobs are inspected
- **THEN** neither declares `continue-on-error` and `release` has no `if:` that can run it after a failed `needs`

#### Scenario: Same version as the tag
- **WHEN** the tag is `v0.4.0` and the wheel carries another version
- **THEN** the verifier fails with a message naming both versions and the release does not run

### Requirement: Documentation states which interpreters and runners verify the wheel
`docs/releasing.md` and `docs/testing.md` SHALL state the Python version and
the runner image of each leg that verifies the wheel, including the macOS leg, the
reason the floor leg is pinned, the constraints of the macOS runner (no Apple
`container` runtime and no virtualization, so only runtime-free checks run there),
and how to maintain the pin when the image or the interpreter reaches end of
support. A documentation test SHALL fail when the documented legs differ from the
workflows.

#### Scenario: Reader checks the coverage
- **WHEN** a maintainer reads `docs/releasing.md`
- **THEN** it names the floor version with its pinned runner image and the newest version with `ubuntu-latest`, and says that the floor is Python 3.9 as long as `requires-python` says so

#### Scenario: Reader checks the macOS coverage
- **WHEN** un mantenedor lee `docs/releasing.md` o `docs/testing.md`
- **THEN** encuentra la imagen de runner de macOS y la versión de Python de la pata de macOS, y la nota de que esa pata no ejecuta el nivel `live`

#### Scenario: Pin drift
- **WHEN** a workflow changes the runner of the floor leg and the documentation still names the old one
- **THEN** the documentation test fails naming both

#### Scenario: macOS drift
- **WHEN** un workflow cambia el runner o la versión de Python de la pata de macOS y la documentación sigue nombrando los anteriores
- **THEN** la prueba de documentación falla nombrando ambos
