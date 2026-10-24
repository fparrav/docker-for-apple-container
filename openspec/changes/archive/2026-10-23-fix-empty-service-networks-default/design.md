## Context

Fuente: sección P2 del issue #2 (sin comentarios ni referencias). Observado en el código actual de la rama:

- `compose_plan.service_network_keys(spec)` devuelve las claves de un mapa o lista tal cual, de modo que una colección vacía da `[]`, y solo devuelve `["default"]` cuando `networks` no es mapa ni lista.
- Esa función alimenta `plan().networks` (`compose_plan.py`, ~143), `compose._service_network_keys` y, a través de él, `_service_networks` (los `--network` de cada contenedor), `_check_resources` (~1898) y `_print_dry_run` (~2525). Corregirla corrige creación, adjuntado, chequeo y dry run a la vez.
- La prueba `test_empty_mapping_contributes_nothing` (`tests/test_compose_plan.py`) espera hoy que `networks: {}` no aporte ninguna red; la prueba `test_official_normalizer_yields_default_for_empty_network_forms` (`tests/test_compose.py`) fija que el normalizador oficial 5.6.0 produce `{"default": null}` para `[]` y `{}`.

## Goals / Non-Goals

**Goals**

- Paridad con el normalizador oficial para `networks: []` y `networks: {}`.
- No crear `default` cuando el servicio especifica solo redes explícitas no vacías.

**Non-Goals**

- Rediseñar la provisión de redes (ya resuelta en `fix-compose-default-network-provisioning`, archivado).
- Cambiar nombres, etiquetas o `down`.

## Decisions

- **D1. Un solo punto de cambio.** Se corrige `service_network_keys`: una colección `networks` vacía (lista o mapa) se trata como ausencia y devuelve `["default"]`. Fuente: criterio "Empty list and empty mapping imply the project's `default` network" (issue, P2) y la prueba de caracterización del normalizador oficial.
- **D2. Las redes no vacías no cambian.** `networks: [edge]`, `{back: {}}` o `[default, back]` conservan su resultado; las externas no crean nada. Fuente: criterios "Preserve correct handling of nonempty explicit/external networks and partial service selection" y "do not create `default` for services that specify only nonempty explicit networks" (issue, P2).
- **D3. Pruebas.** Invertir la prueba de plan que fija el comportamiento incorrecto; agregar casos de plan para `[]` y `{}` con y sin selección parcial y `--no-deps`; agregar casos de runtime falso que lean `created_networks()` y el estado final (creación de `demo_default`, adjuntado con `--network demo_default`) y un caso de `--dry-run` que liste `demo_default` sin crearla. Fuente: criterio "Update pure plan and fake-runtime tests for network creation, attachment and `--dry-run`" (issue, P2). Los ayudantes `created_networks()` y `assert_nothing_was_created()` ya existen en `tests/test_compose.py`.
- **D4. Comparación con el oficial.** Se conserva la prueba de caracterización existente como referencia; no se agrega dependencia nueva. Fuente: criterio "Compare with official Compose behavior" (issue, P2).
- **D5. Consecuencia para la evidencia.** Igual que en `reject-reserved-compose-labels`: un tag final requiere evidencia en vivo nueva generada sobre árbol limpio. Fuente: "Code/test changes invalidate the current live evidence identity" (issue, Implementation guidance).

## Open questions

- **Q1.** ¿Otras formas "vacías" del normalizador (por ejemplo `networks: null` o un mapa cuyos valores son `null`) coinciden ya con el oficial? `networks:` sin valor ya cae en `default` por no ser mapa ni lista; el issue solo menciona `[]` y `{}`, así que no se amplía el alcance.
- **Q2.** Con `network_mode` declarado, el servicio no usa redes de proyecto; el issue no lo trata y se asume que el rechazo existente de `network_mode` no cambia.

## Source mapping

| Requirement o tarea | Fuente |
| --- | --- |
| Requirement (modificado): the implicit default network also covers empty `networks` | issue #2, P2 "Finding" y primer criterio |
| Scenario: lista vacía / mapa vacío crean y adjuntan `default` | issue #2, P2 ejemplo YAML y criterios 1 y 3 |
| Scenario: solo redes no vacías no crea `default` | issue #2, P2 criterios 2 y 4 |
| Scenario: dry run lista `default` para formas vacías | issue #2, P2 criterio 3 (`--dry-run`) |
| Tareas 1.x (pruebas) | issue #2, P2 criterios 2-4; "Add regression tests for each correction" |
| Tarea 3.2 (evidencia) | issue #2, "Implementation guidance" |
