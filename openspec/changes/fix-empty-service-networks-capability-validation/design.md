## Context

Fuente: issue #5 (sin comentarios). Observado en el código de `main` (`e38b739`):

- `compose_plan.service_network_keys(spec)` devuelve `["default"]` para `networks` ausente, `[]` y `{}`; alimenta el plan, la verificación y creación de redes y el adjuntado.
- `compose_capabilities._collect_resources` itera las claves de cualquier lista o mapa sin retroceso a `default`; `_resources` solo revisa lo que se recolectó como usado.
- `compose._ensure_network` aprovisiona la red del plan sin aplicar opciones como `ipam`.
- Un test existente fija que `compose_capabilities` depende solo de `security`, `mounts` y `compose_resource_compat`.

## Goals / Non-Goals

**Goals**

- Que el gate valide exactamente las redes que el plan usará.
- Rechazar antes de cualquier mutación del runtime.

**Non-Goals**

- Cambiar la clasificación de atributos de red ni el normalizador oficial.
- Validar redes declaradas que ningún servicio seleccionado usa.

## Decisions

- **D1. Una sola regla.** `service_network_keys` vive en `compose_resource_compat` y las dos piezas la importan; no quedan dos implementaciones. Fuente: criterio "one shared resolution rule" del issue.
- **D2. Ubicación.** `compose_resource_compat` ya es el módulo puro compartido por el gate y el traductor y está en la lista de dependencias permitidas de `compose_capabilities`; un módulo nuevo exigiría cambiar esa prueba de dependencias, y importar `compose_plan` crearía el ciclo `cli` → `compose_capabilities` → `compose_plan` → `cli`.
- **D3. Atribución.** `_collect_resources` conserva `setdefault`: el primer servicio que usa la red es el dueño del rechazo, sin duplicados.
- **D4. Pruebas.** Unitarias del validador y de runtime falso, con `[]` y `{}`, que comprueban código 64, `REFUSED:` y que no se crea red, volumen ni contenedor; casos de comportamiento vigente (`internal`, labels, redes explícitas, selección parcial, `--no-deps`, perfiles vía normalizador falso, excepción de otra clave).
- **D5. Evidencia.** Un tag final requiere evidencia en vivo nueva sobre el árbol final (el change edita `src/` y `tests/`).

## Open questions

- **Q1.** Las pruebas de perfiles usan un normalizador falso porque el parser integrado rechaza `profiles`; con el oficial el bug no se manifiesta (reescribe `[]` como `{default: null}`).

## Source mapping

| Requirement o tarea | Fuente |
| --- | --- |
| Requirement (modificado): default network validated for empty forms | issue #5, Summary y criterios 1 y 2 |
| Scenario: supported default options still work | issue #5, criterio 3 |
| Scenario: explicit networks and selective `up` | issue #5, criterio 4 |
| D1, D2 | issue #5, criterio 5 |
| Tarea de pruebas | issue #5, criterio 6 |
