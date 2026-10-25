## 1. Pruebas que fallan primero (design D3, D4)

- [x] 1.1 Pruebas del validador y de runtime falso
  - Archivos: `tests/test_compose_empty_network_capabilities.py` (nuevo). Depende de: ninguna.
  - Cambio: casos con `networks: []` y `networks: {}` para `ipam`, `enable_ipv6`, `driver_opts` no soportada y driver no soportado en `networks.default`; `internal` y labels no reservadas siguen pasando; redes explícitas no vacías no validan un `default` sin uso; un servicio sin `networks` sigue validando `default`; mezcla de `[]` con un servicio sin clave sin rechazos duplicados; `up` con runtime falso sale 64 con `REFUSED:` y sin crear red, volumen ni contenedor; `up <servicio>` y `--no-deps` con y sin el servicio de formas vacías; perfiles con un normalizador falso; una excepción de otra clave no levanta el rechazo.
  - Hecho cuando: los casos del rechazo fallan antes de 2.1 por la razón correcta (no se rechaza, `up` sale 0 en vez de 64) y pasan después; los casos de comportamiento vigente pasan antes y después.

## 2. Implementación (design D1-D3)

- [x] 2.1 Compartir la regla de redes de un servicio
  - Archivos: `src/container_docker_shim/compose_resource_compat.py`, `compose_plan.py`, `compose_capabilities.py`. Depende de: 1.1.
  - Cambio: mover `service_network_keys` a `compose_resource_compat`; `compose_plan` la importa y `_collect_resources` la usa en lugar de iterar las claves declaradas. Sin cambios en `compose.py` ni en las pruebas existentes.
  - Hecho cuando: todas las pruebas de 1.1 pasan y `python3 -m unittest discover -s tests` pasa completo.

## 3. Verificación y consecuencias (design D5)

- [x] 3.1 Verificación local completa
  - Archivos: ninguno. Depende de: 2.1.
  - Hecho cuando: `python3 -m unittest discover -s tests -v` pasa; `python3 scripts/test_report.py --tiers unit,fake` termina en 0 (borrar el `report.json` generado); `python3 -m compileall -q src scripts tests` y `git diff --check` están limpios; `openspec validate fix-empty-service-networks-capability-validation --strict` pasa.

- [ ] 3.2 Generar evidencia en vivo nueva antes del primer tag final
  - Archivos: `docs/evidence/runtime-1.5.0-<timestamp>.json` (nuevo). Depende de: 3.1 y de integrar este change junto con cualquier otro que toque `src/`, `scripts/` o `tests/`.
  - Por qué una persona: la corrida usa la máquina del mantenedor y la decisión de confirmar el archivo. No es necesaria para pre-releases.
  - Hecho cuando: `git status --porcelain -- src scripts tests pyproject.toml` no imprime nada, `python3 scripts/validate-runtime.py --evidence` termina en 0 con todas las pruebas declaradas aprobadas y `python3 scripts/verify_release_evidence.py --tag v9.9.9` termina en 0. Los archivos de evidencia anteriores no se modifican.
