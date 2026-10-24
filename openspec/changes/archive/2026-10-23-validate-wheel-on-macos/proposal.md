## Why

El shim está pensado para macOS 26, pero los smoke tests de la wheel (`scripts/verify_wheel.py`) corren hoy solo en Linux, en el job `wheel` de `ci.yml` y en el job `smoke` de `release.yml` (`ubuntu-24.04` con Python 3.9 y `ubuntu-latest` con la versión más reciente). La suite en vivo ejercita el shim desde el código fuente sobre Apple `container`, no la wheel distribuida. Por tanto, el artefacto que se publica no ha sido instalado ni ejercitado de forma independiente en macOS: un problema de instalación, de entry point o de importación que dependa de la plataforma solo se descubriría después de publicar.

## What Changes

- Agregar a los smoke tests de la wheel una pata en macOS que instale la wheel en un entorno virtual limpio, la importe desde `site-packages`, ejecute `docker version` y compruebe el error limpio cuando falta el runtime, idealmente sobre el mismo artefacto que se va a publicar.
- Mantener esos smoke tests sin runtime separados de la suite en vivo de Apple `container 1.5.0`, sin requerir Docker Desktop y sin escribir ni contar evidencia en vivo.
- Documentar las versiones de macOS y de Python validadas y las restricciones del runner.
- Hacer que un fallo de la pata de macOS impida el release, con la misma regla de "sin bypass" que ya rige a las patas de Linux.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `fork-distribution`: la wheel se instala y se ejercita también en macOS antes de publicar; el job `smoke` incluye una pata de macOS que bloquea el release; la documentación nombra esa pata.
- `release-evidence`: el resultado del smoke test de macOS tampoco es evidencia en vivo.

## Impact

- `.github/workflows/ci.yml` (job `wheel`) y `.github/workflows/release.yml` (job `smoke`).
- `docs/releasing.md` y `docs/testing.md`.
- `tests/test_workflows.py` y `tests/test_docs.py` (formas de la matriz y coherencia de la documentación con los workflows); posible ajuste menor en `scripts/verify_wheel.py` si la primera ejecución en macOS descubre algún problema de portabilidad.
- Sin cambios en el código del shim ni en la suite en vivo.
- Evidencia en vivo: editar `tests/` o `scripts/` cambia el hash de árbol registrado y un tag final necesita evidencia nueva; los cambios confinados a `.github/workflows/`, `docs/`, `README.md` y `openspec/` no la invalidan (ver `design.md`).

## Out of scope

- Ejecutar la suite en vivo de Apple `container` en runners alojados de GitHub (no tienen virtualización).
- Requerir Docker Desktop.
- Publicar la wheel por otro canal o firmarla.
- La limpieza documentada de volúmenes anónimos huérfanos históricos con `docker compose down -v`.

## Order

Último de tres changes derivados del mismo issue (P1 → P2 → P3); el issue lo describe como un cambio pequeño e independiente de empaquetado y CI. Puede implementarse en cualquier momento sin esperar a P1 ni P2.

## Source

- https://github.com/fparrav/docker-for-apple-container/issues/2 — "[Audit follow-up] Prioritize Compose label safety, empty networks, and macOS wheel validation" (open, sección P3, fetched 2026-10-23)
