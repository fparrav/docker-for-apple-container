## Why

`fix-empty-service-networks-default` hizo que el plan trate `networks: []` y `networks: {}` como la red implícita `default` (`compose_plan.service_network_keys`). La validación de capacidades quedó con otra regla: `compose_capabilities._collect_resources` itera las claves de cualquier lista o mapa, de modo que una colección vacía registra cero redes y el `default` nunca se revisa. Un `networks.default` de nivel superior con opciones sin equivalente en Apple `container` (`ipam`, `enable_ipv6`, `driver_opts` no soportadas, drivers no soportados) escapa de la validación fail-closed, y luego `up` crea la red sin aplicar esa configuración.

Es un hallazgo de revisión estática del código (issue #5), no una reproducción en Apple `container`.

## What Changes

- Una sola regla de resolución de las redes de un servicio: `service_network_keys` pasa a `compose_resource_compat` (módulo puro que ya comparten el gate y el traductor) y la usan `compose_plan` y `compose_capabilities`. No se mueve a un módulo nuevo porque `compose_plan` importa `cli`, que a su vez importa `compose_capabilities`.
- `_collect_resources` registra `default` para `networks: []` y `networks: {}`, igual que para un servicio sin `networks`.
- Las opciones no soportadas de `networks.default` se rechazan con la convención `REFUSED:` y código 64, antes de crear red, volumen o contenedor.
- Pruebas: validador y runtime falso, con `[]` y `{}`; `internal` y labels no reservadas siguen funcionando; redes explícitas no vacías, `up <servicio>` y `--no-deps` no validan un `default` sin uso.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `compose-capability-validation`: el `networks.default` implícito se valida cuando un servicio seleccionado lo usa, también con una lista o un mapa de redes vacío.

## Impact

- `src/container_docker_shim/compose_resource_compat.py`, `compose_plan.py`, `compose_capabilities.py`; `tests/test_compose_empty_network_capabilities.py` (nuevo).
- Sin cambios en el normalizador oficial, en la provisión de redes ni en `down`.
- Evidencia en vivo: el change edita `src/` y `tests/`, por lo que un tag final necesita evidencia nueva sobre el árbol correspondiente.

## Out of scope

- Rediseño de redes y la regla ya corregida de que redes explícitas no vacías no aprovisionan `default`.
- Requisito de Docker Engine.

## Source

- https://github.com/fparrav/docker-for-apple-container/issues/5 — "[P1] Reject unsupported default network options when service networks are empty" (open, fetched 2026-10-24). Relacionado: issue #2.
