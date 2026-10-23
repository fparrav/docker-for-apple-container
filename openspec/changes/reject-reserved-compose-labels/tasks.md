## 1. Pruebas que fallan primero (design D2, D3, D6)

- [x] 1.1 Agregar las pruebas de rechazo de etiquetas reservadas @route:pi-qwen/xhigh
  - Archivos: `tests/test_compose.py`. Depende de: ninguna.
  - Cambio: una clase de pruebas con el runtime falso y `write_compose`. Casos: clave reservada en servicio como mapa y como lista; en red y en volumen, en ambas formas; varias claves ofensivas informadas juntas; el error contiene la clave y el nombre del servicio o recurso; `--dry-run` produce el mismo error y código. Cada caso rechazado termina con `assert_nothing_was_created()` y comprueba que el estado final del runtime falso no cambió.
  - Hecho cuando: los casos fallan antes de 2.1 por la razón correcta (se llama a `container run`/`network create`/`volume create` con la etiqueta duplicada) y pasan después.

- [x] 1.2 Agregar las pruebas de no regresión @route:pi-qwen/xhigh
  - Archivos: `tests/test_compose.py`. Depende de: ninguna.
  - Cambio: etiquetas de usuario no reservadas (`team=platform`, `com.example.owner=ops`) en servicio, red y volumen aparecen una sola vez junto a las etiquetas del shim; un servicio sin etiquetas conserva las seis etiquetas internas; una clave parecida pero fuera del namespace (según la decisión de Q1) no se rechaza.
  - Hecho cuando: pasan antes y después de 2.1.

- [x] 1.3 Cubrir el camino del normalizador oficial @route:pi-qwen/xhigh
  - Archivos: `tests/test_compose.py`. Depende de: 1.1.
  - Cambio: una prueba que alimenta al shim con el JSON que produce el normalizador oficial (mapa de etiquetas) con una clave reservada y afirma el mismo rechazo; se omite con `skipTest` si `docker-compose` no está disponible, igual que las demás pruebas de caracterización.
  - Hecho cuando: el caso pasa con el normalizador disponible y se omite limpiamente sin él.

- [x] 1.4 Cubrir los hallazgos de la revisión independiente en las pruebas @route:pi-qwen/xhigh
  - Archivos: `tests/test_compose.py`. Depende de: 1.1, 1.3.
  - Cambio: pruebas de que se rechaza una clave reservada en una red o volumen declarado pero no usado por ningún servicio del plan y en uno `external: true` (map y lista); la clave exacta `com.docker.compose` y una entrada de lista sin `=`; y, con una red, un volumen y un contenedor preexistentes en el estado del runtime falso, que tras el rechazo siguen idénticos y que `runtime_calls()` no contiene ninguna llamada que cree, modifique o elimine.
  - Hecho cuando: los casos de recursos no usados o externos fallan antes de 2.2 por la razón correcta y los demás pasan antes y después.

## 2. Implementación (design D1-D5)

- [x] 2.1 Rechazar las claves reservadas en la validación previa @route:claude-sonnet/medium
  - Archivos: `src/container_docker_shim/compose.py`. Depende de: 1.1, 1.2.
  - Cambio: función pura que recibe el valor de `labels` (mapa o lista) y devuelve las claves dentro del namespace; `_validate_plan` la aplica a los servicios del plan y a las redes y volúmenes que fije Q3, y agrega una línea `REFUSED:` por clave con recurso. No tocar `_build_run_args`, `_ensure_network` ni `_ensure_named_volume` salvo que la prueba lo requiera.
  - Hecho cuando: todas las pruebas de 1.x pasan, `python3 -m unittest discover -s tests -v` pasa con `python3` y `/usr/bin/python3`, y `git diff` solo toca `compose.py` y los archivos de prueba.

- [x] 2.2 Validar todas las redes y volúmenes declarados @route:claude-sonnet/medium
  - Archivos: `src/container_docker_shim/compose.py`. Depende de: 1.4, 2.1.
  - Cambio: `_reserved_label_problems` revisa las etiquetas de todos los `project.networks` y `project.volumes` declarados (usados o no, gestionados o `external`), además de las de los servicios del plan; mismo formato de línea `REFUSED:`.
  - Hecho cuando: todas las pruebas de 1.x pasan y `python3 -m unittest discover -s tests` pasa con `python3` y `/usr/bin/python3`.

## 3. Documentación

- [x] 3.1 Documentar el namespace reservado @route:pi-qwen/xhigh
  - Archivos: `README.md` y/o la guía de Compose que describa las etiquetas (confirmar cuál al implementar). Depende de: 2.1.
  - Cambio: indicar que `com.docker.compose.*` es del shim y que se rechaza en `labels` de servicios, redes y volúmenes.
  - Hecho cuando: `python3 scripts/render_compat_table.py --check README.md` sigue pasando.

## 4. Verificación y consecuencias (design D7)

- [x] 4.1 Comprobar cómo resuelve Apple `container` 1.5.0 los `--label` duplicados @needs-human
  - Archivos: ninguno (resultado en el reporte o en las notas del change). Depende de: ninguna.
  - Por qué una persona: requiere la máquina con macOS 26 y `container 1.5.0` en ejecución; usar un recurso desechable con un nombre único, sin tocar cargas ajenas ni usar prune global.
  - Hecho cuando: queda anotado si gana la primera, la última o falla, y se cierra la pregunta Q4.

- [x] 4.2 Verificación local completa @worker:orchestrator
  - Archivos: ninguno. Depende de: 2.1, 3.1.
  - Hecho cuando: `python3 -m unittest discover -s tests` y `/usr/bin/python3 -m unittest discover -s tests` pasan, `python3 scripts/test_report.py --tiers unit,fake` termina en 0 (borrar el `report.json` generado), `python3 -m compileall -q src scripts tests` y `git diff --check` están limpios, y `openspec validate reject-reserved-compose-labels --strict` pasa.

- [x] 4.3 Generar evidencia en vivo nueva antes del primer tag final @needs-human
  - Archivos: `docs/evidence/runtime-1.5.0-<timestamp>.json` (nuevo). Depende de: 4.2 y de integrar este change junto con `fix-empty-service-networks-default` y cualquier otro que toque `src/`, `scripts/` o `tests/`.
  - Por qué una persona: la corrida usa la máquina del mantenedor y la decisión de confirmar el archivo. No es necesaria para pre-releases.
  - Hecho cuando: `git status --porcelain -- src scripts tests pyproject.toml` no imprime nada, `python3 scripts/validate-runtime.py --evidence` termina en 0 con todas las pruebas declaradas aprobadas, y `python3 scripts/verify_release_evidence.py --tag v9.9.9` termina en 0. Los archivos de evidencia anteriores no se modifican.
