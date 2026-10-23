## 1. Pruebas que fallan primero (design D1-D4)

- [x] 1.1 Corregir la expectativa de plan y agregar las pruebas de plan
  - Archivos: `tests/test_compose_plan.py`. Depende de: ninguna.
  - Cambio: reemplazar `test_empty_mapping_contributes_nothing`, que fija el comportamiento incorrecto, por casos que esperan `["default"]` para `networks: []` y `networks: {}`; agregar casos con selección parcial y `no_deps`, con una forma vacía junto a `networks: [edge]`, y casos que confirman que redes no vacías (`[edge]`, `{back: {}}`, `[default, back]`) no cambian.
  - Hecho cuando: los casos de formas vacías fallan antes de 2.1 por la razón correcta y pasan después; los demás casos del plan pasan antes y después.

- [x] 1.2 Agregar las pruebas de runtime falso
  - Archivos: `tests/test_compose.py`. Depende de: ninguna.
  - Cambio: en la clase de pruebas de redes implícitas, casos con `created_networks()` y el estado final de `state["networks"]`: `networks: []` crea `demo_default` y el contenedor lleva `--network demo_default`; lo mismo con `networks: {}`; forma vacía junto a un servicio con `[back]` crea ambas redes una vez; solo `[edge]` externa o solo `[back]` no crea `demo_default`; `up api` con la forma vacía en `web` no seleccionado no crea `demo_default`; `--dry-run` lista `demo_default` una vez y `assert_nothing_was_created()` pasa.
  - Hecho cuando: los casos de formas vacías fallan antes de 2.1 y pasan después; los casos de redes explícitas pasan antes y después.

## 2. Implementación (design D1, D2)

- [x] 2.1 Tratar las colecciones de redes vacías como `default`
  - Archivos: `src/container_docker_shim/compose_plan.py`. Depende de: 1.1, 1.2.
  - Cambio: en `service_network_keys`, si `networks` es un mapa o lista vacío, devolver `["default"]`; actualizar el docstring (hoy dice que una colección vacía no aporta redes). No cambiar `compose.py` salvo que una prueba lo requiera.
  - Hecho cuando: todas las pruebas de 1.x pasan, `python3 -m unittest discover -s tests -v` pasa con `python3` y `/usr/bin/python3`, y `git diff` solo toca `compose_plan.py` y los archivos de prueba.

## 3. Verificación y consecuencias (design D4, D5)

- [x] 3.1 Verificación local completa y comparación con el oficial
  - Archivos: ninguno. Depende de: 2.1.
  - Hecho cuando: la prueba de caracterización del normalizador oficial pasa (con `docker-compose` 5.6.0 disponible) y coincide con el comportamiento del shim para ambas formas; `python3 scripts/test_report.py --tiers unit,fake` termina en 0 (borrar el `report.json` generado); `python3 -m compileall -q src scripts tests` y `git diff --check` están limpios; `openspec validate fix-empty-service-networks-default --strict` pasa.

- [ ] 3.2 Generar evidencia en vivo nueva antes del primer tag final @needs-human
  - Archivos: `docs/evidence/runtime-1.5.0-<timestamp>.json` (nuevo). Depende de: 3.1 y de integrar este change junto con `reject-reserved-compose-labels` y cualquier otro que toque `src/`, `scripts/` o `tests/`.
  - Por qué una persona: la corrida usa la máquina del mantenedor y la decisión de confirmar el archivo. No es necesaria para pre-releases.
  - Hecho cuando: `git status --porcelain -- src scripts tests pyproject.toml` no imprime nada, `python3 scripts/validate-runtime.py --evidence` termina en 0 con todas las pruebas declaradas aprobadas y `python3 scripts/verify_release_evidence.py --tag v9.9.9` termina en 0. Los archivos de evidencia anteriores no se modifican.
