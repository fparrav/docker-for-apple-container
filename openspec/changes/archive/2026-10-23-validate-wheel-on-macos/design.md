## Context

Fuente: sección P3 del issue #2 (sin comentarios ni referencias). Observado en el repositorio:

- `ci.yml`, job `wheel`: matriz con `3.9` en `ubuntu-24.04` y `3.x` en `ubuntu-latest`; construye la wheel y corre `python3 scripts/verify_wheel.py --dist dist`.
- `release.yml`: `package` construye una sola vez y sube el artefacto `dist`; `smoke` (misma matriz de Linux) descarga ese artefacto y corre `verify_wheel.py --dist dist --expect-version "${TAG#v}"`; `release` depende de `[checks, smoke]` y publica los mismos archivos.
- `scripts/verify_wheel.py` ya instala con `pip install --no-index --no-deps` en un entorno virtual nuevo, importa el paquete desde `site-packages`, ejecuta `docker version` con un `container` de reemplazo y comprueba el error de "Apple container CLI not found" cuando falta el runtime. Usa `#!/bin/sh` para el reemplazo y `venv_bin` ya distingue `Scripts` de `bin`.
- `docs/releasing.md` indica que los runners alojados no pueden correr el nivel `live` (sin virtualización) y que la validación completa en Apple `container` ocurre en un runner autoalojado de macOS 26.

## Goals / Non-Goals

**Goals**

- Ejercitar en macOS la wheel que se publica, con las mismas comprobaciones sin runtime que ya corren en Linux.
- Que un fallo en macOS impida el release.
- Dejar documentado qué macOS y qué Python se validan.

**Non-Goals**

- Ejecutar la suite en vivo en runners alojados.
- Requerir Docker Desktop.

## Decisions

- **D1. Pata de macOS en `smoke`.** Se agrega una pata de macOS a la matriz de `smoke` en `release.yml`; descarga el mismo artefacto `dist` que construyó `package`, así que se prueba el artefacto exacto del release, y `release` ya depende de todas las patas de `smoke`. Fuente: "ideally test the exact artifact intended for release" y "Ensure required smoke failures prevent release" (issue, P3).
- **D2. Pata de macOS en el job `wheel` de CI.** Se agrega la misma pata en `ci.yml` para detectar el problema antes de un tag. Fuente: "Current CI smoke tests install wheels on Linux" (issue, P3, Gap). Es una pregunta abierta si va solo en release (Q2).
- **D3. Reutilizar `verify_wheel.py` sin cambiar su contrato.** Instalación, importación, `docker version` y error limpio sin runtime ya son sus comprobaciones. Si la primera ejecución en macOS descubre una dependencia de plataforma, se corrige el script con prueba en `tests/test_verify_wheel.py`. Fuente: "Run macOS smoke checks for installation, imports, `docker version` and clean runtime-absent errors" (issue, P3).
- **D4. Separación de la suite en vivo.** El smoke de macOS no usa el runtime de Apple, no escribe en `docs/evidence/` y no suma a los conteos `live`; la suite en vivo contra `container 1.5.0` no cambia. Fuente: "Keep runtime-free packaging smoke tests separate from the live Apple `container 1.5.0` suite; do not require Docker Desktop" (issue, P3); requisito vigente "Packaging checks are not live evidence" (`release-evidence`).
- **D5. Documentación y su prueba.** `docs/releasing.md` y `docs/testing.md` listan la pata de macOS con su imagen de runner y su versión de Python, y las restricciones del runner; la prueba de documentación existente (`tests/test_docs.py`) se amplía para que falle si difieren de los workflows. Fuente: "Document validated macOS/Python versions and runner constraints" (issue, P3); requisito vigente de documentación en `fork-distribution`.
- **D6. Pruebas de workflow.** Las pruebas de `tests/test_workflows.py` que fijan la forma de la matriz y las reglas de `smoke` y `release` se ajustan a la pata nueva y mantienen que ningún job use `continue-on-error`, `if:` que lo salte ni runner autoalojado. Fuente: requisitos vigentes de `fork-distribution` ("The release publishes only the artifact every smoke leg installed").
- **D7. Consecuencia para la evidencia.** Los cambios a `.github/workflows/` y `docs/` no invalidan la evidencia, pero editar `tests/` (D5, D6) y, si ocurre, `scripts/verify_wheel.py` sí cambia el hash de árbol; un tag final necesita entonces evidencia en vivo nueva, generada una sola vez al integrar los tres changes. Fuente: "Code/test changes invalidate the current live evidence identity" (issue, Implementation guidance) y requisito vigente "Packaging verifier changes invalidate the evidence".

## Open questions

- **Q1.** (Resuelta, 2026-10-23.) Se usa `macos-latest` con Python `3.x`, igual que la pata `ubuntu-latest`: sigue a GitHub y no fija un macOS concreto. No se verificó qué versión de macOS apunta hoy ni si existe un runner alojado de macOS 26; la tarea 4.2 lo confirma en Actions. Si la imagen cambia y rompe la comprobación, se fija una imagen `macos-*` versionada en ambos workflows (documentado en `docs/releasing.md`).
- **Q2.** (Resuelta.) La pata va en `release.yml` (`smoke`) y en `ci.yml` (`wheel`), con la misma matriz; `tests/test_workflows.py` ya exige que ambas coincidan.
- **Q3.** (Resuelta.) macOS cubre solo la versión más reciente (`3.x`); el piso 3.9 sigue en Linux, donde el manifiesto de `setup-python` lo garantiza.
- **Q4.** (Resuelta, 2026-10-23.) La wheel es pura (`Tag: py3-none-any`, `Root-Is-Purelib: true`). Construida con `python -m build` y verificada con `scripts/verify_wheel.py` en esta máquina (macOS, arm64, Python 3.12.7), pasó todas las comprobaciones sin cambios al script; por eso no hace falta construirla en macOS ni tocar `verify_wheel.py`.

## Source mapping

| Requirement o tarea | Fuente |
| --- | --- |
| Requirement (agregado): the release wheel is installed and exercised on macOS | issue #2, P3 "Gap" y primer criterio |
| Scenario: el artefacto exacto del release se instala en macOS | issue #2, P3 criterio 1 ("exact artifact") |
| Scenario: sin Docker Desktop ni runtime de Apple; separado de la suite en vivo | issue #2, P3 criterio 2 |
| Requirement (modificado): documentación nombra macOS/Python y restricciones | issue #2, P3 criterio 3 |
| Scenario: un fallo en macOS impide el release | issue #2, P3 criterio 4 |
| Requirement (modificado): the release publishes only the artifact every smoke leg installed | issue #2, P3 criterio 4; requisito vigente de `fork-distribution` |
| Requirement (modificado): packaging checks are not live evidence | issue #2, P3 criterio 2; requisito vigente de `release-evidence` |
| Tareas de evidencia | issue #2, "Implementation guidance" |
