## Context

Fuente: sección P1 del issue #2 (hallazgo de auditoría sobre `main` en `541c02483d62`; CI verde y 40/40 pruebas en vivo con `container 1.5.0`, que el issue pide no tratar como fallos de esas ejecuciones). El issue no tiene comentarios ni referencias.

Observado en el código actual:

- `_build_run_args` (`compose.py`, ~1120-1160) emite `--label` para `LABEL_PROJECT`, `LABEL_SERVICE`, `LABEL_NUMBER`, `LABEL_ONEOFF` y, si existen, `LABEL_CONFIG_HASH` y `LABEL_IMAGE`; después recorre `spec["labels"]` (mapa o lista) y anexa cada entrada tal cual.
- `_ensure_network` y `_ensure_named_volume` emiten el label de proyecto y el de clave del recurso, y después anexan `_resource_label_args(spec.get("labels"))` con las etiquetas del usuario.
- `_validate_plan` ya concentra los rechazos previos a cualquier efecto (tabla de capacidades, montajes, secretos de build, dependencias), los imprime como líneas `REFUSED:` y sale con 64 sin crear nada. `--dry-run` pasa por el mismo camino.

## Goals / Non-Goals

**Goals**

- Que ninguna etiqueta de usuario pueda duplicar o pisar una etiqueta de identidad del shim.
- Fallar cerrado y antes de mutar, con un mensaje accionable.
- Conservar el modelo sin estado: la propiedad sigue leyéndose de las etiquetas.

**Non-Goals**

- Resolver o documentar cómo Apple `container` trata etiquetas duplicadas (solo se verifica, ver tarea 4.1).
- Cambiar las etiquetas internas o el `down`.

## Decisions

- **D1. Namespace reservado.** Una clave es reservada si es `com.docker.compose` o empieza por `com.docker.compose.`. Fuente: "reserved `com.docker.compose` namespace" (issue, P1). El límite exacto del prefijo es una pregunta abierta (Q1).
- **D2. Una sola función de comprobación.** Una función pura recibe un valor de `labels` (mapa o lista) y devuelve las claves reservadas; la usan los tres sitios (servicios, redes, volúmenes). Fuente: "support map and list forms" (issue, P1). En la forma de lista la clave es la parte anterior al primer `=`; una entrada sin `=` es una clave sin valor.
- **D3. Validar en `_validate_plan`.** Los rechazos se acumulan con los demás y salen con el mismo formato `REFUSED:`, antes de cualquier llamada al runtime. Fuente: "Validate before any runtime mutation, with explicit offending key and service/resource in the error" (issue, P1). Esto también cumple el requisito vigente "Dry run reports the same problems as up" sin código adicional; el código de salida (64) es coherente con los demás rechazos pero es una pregunta abierta (Q2).
- **D4. Las etiquetas internas no pasan por el rechazo.** La comprobación se hace sobre `labels` declaradas por el usuario en el modelo, nunca sobre los argumentos finales. Fuente: "Keep internally generated Compose labels valid" (issue, P1).
- **D5. Misma validación para ambos parsers.** Se valida sobre el `Project` ya cargado, sea del parser integrado o del normalizador oficial. Fuente: "Cover built-in parser and official-normalizer paths" (issue, P1). El normalizador oficial convierte listas en mapas, así que sus duplicados desaparecen antes de llegar al shim; el parser integrado conserva la lista tal cual.
- **D6. Pruebas.** Casos para mapa y lista en servicio, red y volumen; una etiqueta no reservada válida junto a una reservada; el caso sin conflicto sigue funcionando; el final de la prueba afirma que `assert_nothing_was_created()` pasa y que el estado del runtime falso no cambió. Fuente: "Assert no resources are created or altered when rejecting the configuration" (issue, P1).
- **D7. Consecuencia para la evidencia.** Editar `src/` y `tests/` cambia el hash de árbol, por lo que un tag final requiere evidencia en vivo nueva (`docs/evidence/`), generada sobre árbol limpio y solo después de integrar este y cualquier otro change que toque esos directorios. Fuente: "Code/test changes invalidate the current live evidence identity" (issue, Implementation guidance).

## Open questions

- **Q1.** ¿El límite del prefijo es exactamente `com.docker.compose` y `com.docker.compose.*` (D1), o también debe rechazarse algo como `com.docker.composer`? El issue dice "namespace" sin precisar.
- **Q2.** ¿Código de salida 64 con línea `REFUSED:` (D3) o 1? El issue pide el mensaje con clave y recurso pero no el código.
- **Q3 (resuelta en la revisión: se validan todos los declarados, usados o no, gestionados o externos).** ¿Qué redes y volúmenes se validan: solo los gestionados y usados por los servicios seleccionados (los que `up` crearía) o todos los declarados en el archivo? Para recursos `external: true` el shim no crea nada ni aplica etiquetas, así que el riesgo de pisar identidad no existe; el issue no distingue.
- **Q4.** (Resuelta, 2026-10-23.) Con `container 1.5.0` en macOS 26, una clave `--label` repetida no falla y gana la última: `container create`, `volume create` y `network create` con `--label k=first --label k=second` dejaron `k=second`. Por eso una etiqueta de usuario reservada, si llegara después de la del shim, podría sobrescribirla en silencio; el rechazo previo evita ese riesgo. Se probó con recursos `shimtest-duplabel-*` desechables, ya eliminados.
- **Q5.** ¿`docker run`/`create` con `-l com.docker.compose.*` queda fuera? El issue habla solo de Compose; se deja fuera de alcance salvo que se indique lo contrario.
- **Q6.** Docker Compose oficial: ¿rechaza o acepta estas etiquetas? El issue no lo afirma y no se verificó; comparar antes de fijar el texto del mensaje.

## Source mapping

| Requirement o tarea | Fuente |
| --- | --- |
| Requirement: Reserved Compose labels are refused before anything is created | issue #2, P1 "Finding" y primer criterio de aceptación |
| Scenario: reserved key en servicio, red y volumen (mapa y lista) | issue #2, P1 criterio 1; ejemplo YAML del servicio `api` |
| Scenario: el error nombra la clave y el recurso, sin crear nada | issue #2, P1 criterios 2 y 5 |
| Scenario: etiquetas internas y de usuario válidas siguen funcionando | issue #2, P1 criterios 3 y 4 |
| Scenario: `--dry-run` informa el mismo problema | requisito vigente "Dry run reports the same problems as up" (`compose-dry-run`) |
| Tareas 1.x (pruebas) | issue #2, P1 criterios 4 y 5; "Add regression tests for each correction" |
| Tarea 4.1 (verificación en vivo de duplicados) | issue #2, P1 "Risk" (resolución de duplicados por probar) |
| Tarea 4.2 (evidencia) | issue #2, "Implementation guidance" |
