## Why

El shim escribe etiquetas internas `com.docker.compose.*` (proyecto, servicio, número de contenedor, `oneoff`, hash de configuración, imagen, clave de red y de volumen) y después agrega las etiquetas que el usuario declara en el Compose file, sin rechazar claves reservadas. Un servicio con `labels: {com.docker.compose.project: otro-proyecto}` produce argumentos `--label` duplicados y en conflicto hacia Apple `container`. Lo mismo ocurre en redes y volúmenes con nombre.

Esas etiquetas son el único registro de identidad y propiedad (no hay estado propio del shim): descubrimiento, reconciliación y limpieza (`down`) se apoyan en ellas. Que un usuario pueda pisarlas, aunque sea por accidente, pone en riesgo la identidad del recurso y su eliminación. Que los argumentos duplicados lleguen al runtime está confirmado por inspección estática; **cómo los resuelve Apple `container` 1.5.0 está sin probar** (ver `design.md`, preguntas abiertas).

## What Changes

- Rechazar, antes de cualquier mutación del runtime, toda etiqueta de usuario dentro del namespace reservado `com.docker.compose` en servicios, redes y volúmenes, en forma de mapa y en forma de lista.
- El error nombra la clave ofensiva y el servicio o recurso donde aparece, y se informa junto al resto de rechazos de validación.
- Las etiquetas internas que el shim genera siguen siendo válidas y no pasan por este rechazo.
- Las etiquetas de usuario fuera del namespace reservado siguen funcionando igual.
- La validación se aplica por igual al modelo del parser integrado y al del normalizador oficial, y `--dry-run` informa el mismo problema que `up`.
- Pruebas de regresión que verifican que, al rechazar, no se crea ni se altera ningún recurso.

**BREAKING (menor)**: un Compose file que hoy declare claves `com.docker.compose.*` propias deja de aceptarse y debe renombrarlas.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `compose-resource-identity`: se agrega el requisito de que el namespace de etiquetas `com.docker.compose` es del shim y las etiquetas de usuario que lo usen se rechazan antes de crear recursos.

## Impact

- `src/container_docker_shim/compose.py`: `_validate_plan` (rechazo previo a efectos), `_build_run_args` y `_resource_label_args` (puntos donde hoy se anexan las etiquetas de usuario).
- `tests/test_compose.py` (y `tests/test_compose_plan.py` si la validación vive en el plan).
- Sin cambios en `down`, en la nomenclatura de recursos ni en las etiquetas internas.
- Evidencia en vivo: el cambio edita `src/` y `tests/`, así que el hash de árbol registrado deja de coincidir y un tag final necesita evidencia nueva (ver `design.md`). Los pre-releases no se ven afectados.

## Out of scope

- La limpieza documentada de volúmenes anónimos huérfanos históricos con `docker compose down -v` (el issue la excluye expresamente).
- Cambiar el esquema de etiquetas internas o la forma en que `down` decide la propiedad.
- Etiquetas de `docker run`/`create` fuera de Compose (el issue habla solo de Compose; ver preguntas abiertas).

## Order

Primero de tres changes derivados del mismo issue, en el orden que fija el issue: este (P1, seguridad de propiedad) → `fix-empty-service-networks-default` (P2) → `validate-wheel-on-macos` (P3). No hay dependencia técnica entre ellos; el orden es de prioridad.

## Source

- https://github.com/fparrav/docker-for-apple-container/issues/2 — "[Audit follow-up] Prioritize Compose label safety, empty networks, and macOS wheel validation" (open, sección P1, fetched 2026-10-23)
