## Why

El parser integrado trata `networks: []` y `networks: {}` como "ningún network": `service_network_keys()` (`compose_plan.py`) devuelve una lista vacía para ambas formas y solo cae en `default` cuando la clave `networks` falta. El normalizador oficial de Compose los trata distinto: ambas formas se normalizan a `{"default": null}`, es decir, el servicio usa la red implícita `default` del proyecto. Con `networks: []` el servicio se crea sin red de proyecto y `<project>_default` no se crea, a diferencia de Docker Compose.

La divergencia está fijada hoy por una prueba de plan (`test_empty_mapping_contributes_nothing`, `tests/test_compose_plan.py`) que espera el comportamiento incorrecto, mientras que la prueba de caracterización del normalizador oficial (`tests/test_compose.py`) espera `{"default": null}`. Este change corrige el parser integrado y alinea ambas pruebas.

## What Changes

- `service_network_keys()` devuelve `["default"]` para `networks: []` y `networks: {}`, igual que para un servicio sin `networks`.
- Los servicios que nombran solo redes explícitas no vacías (gestionadas o externas) siguen sin crear ni verificar `<project>_default`.
- Como `up`, el chequeo de recursos, el dry run y el adjuntado de redes leen la misma función, los cuatro quedan alineados con el único cambio.
- Actualizar la prueba de plan que hoy espera lo contrario y agregar pruebas de plan y de runtime falso para creación, adjuntado y `--dry-run`.
- Comparar el resultado con el normalizador oficial para ambas formas vacías, sin crear `default` cuando el servicio solo menciona redes explícitas no vacías.

**BREAKING (menor)**: un servicio con `networks: []` o `networks: {}` en el parser integrado ahora se adjunta a `<project>_default`, que `up` crea si no existe.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `compose-resource-identity`: la red implícita `default` también se usa cuando un servicio declara una lista o un mapa de redes vacío.
- `compose-dry-run`: la lista de redes del dry run incluye `default` para esos servicios, igual que `up`.

## Impact

- `src/container_docker_shim/compose_plan.py` (`service_network_keys`); `compose.py` lo consume sin cambios previstos.
- `tests/test_compose_plan.py` (se reemplaza la expectativa incorrecta), `tests/test_compose.py` (pruebas de runtime falso).
- Sin cambios en la selección parcial de servicios, en `--no-deps`, en redes externas, en `down` ni en la nomenclatura.
- Evidencia en vivo: el cambio edita `src/` y `tests/`, por lo que un tag final necesita evidencia nueva (ver `design.md`). Los pre-releases no se ven afectados.

## Out of scope

- La limpieza documentada de volúmenes anónimos huérfanos históricos con `docker compose down -v` (el issue la excluye).
- Otros desajustes entre el parser integrado y el normalizador oficial que no sean las dos formas vacías.

## Order

Segundo de tres changes derivados del mismo issue (P1 → P2 → P3). No depende técnicamente de `reject-reserved-compose-labels`; ambos tocan `src/` y `tests/`, así que la evidencia en vivo se genera una sola vez al final.

## Source

- https://github.com/fparrav/docker-for-apple-container/issues/2 — "[Audit follow-up] Prioritize Compose label safety, empty networks, and macOS wheel validation" (open, sección P2, fetched 2026-10-23)
