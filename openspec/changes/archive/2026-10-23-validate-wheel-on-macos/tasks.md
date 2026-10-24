## 1. Sondeo local en macOS (design D3, Q4)

- [x] 1.1 Ejecutar `verify_wheel.py` en esta máquina macOS y registrar el resultado
  - Archivos: ninguno (resultado en las notas del change). Depende de: ninguna.
  - Cambio: construir la wheel (`python3 -m build`) y correr `python3 scripts/verify_wheel.py --dist dist`; comprobar el tag de plataforma de la wheel (`py3-none-any` o no, Q4) y anotar cualquier fallo de portabilidad. Borrar `dist/` y `build/` generados si no estaban versionados.
  - Hecho cuando: queda anotado si pasa tal cual o qué falla, y se cierra Q4.

## 2. Pruebas que fallan primero (design D5, D6)

- [x] 2.1 Ajustar las pruebas de workflow
  - Archivos: `tests/test_workflows.py`. Depende de: ninguna.
  - Cambio: afirmar que la matriz de `smoke` en `release.yml` y la del job `wheel` en `ci.yml` incluyen una pata de macOS (runner y Python según Q1), que `smoke` sigue descargando el artefacto `dist` y que no hay `continue-on-error`, `if:` que salte la pata ni runner autoalojado. Mantener las reglas vigentes de pins por SHA y permisos.
  - Hecho cuando: las pruebas nuevas fallan antes de 3.1 y pasan después; las demás pruebas de workflow pasan antes y después.

- [x] 2.2 Ampliar la prueba de documentación
  - Archivos: `tests/test_docs.py`. Depende de: ninguna.
  - Cambio: la prueba que compara la pata del piso con los workflows también compara la pata de macOS (runner y Python) con `docs/releasing.md` y `docs/testing.md`, y falla nombrando ambos valores cuando difieren.
  - Hecho cuando: falla antes de 3.2 y pasa después.

## 3. Workflows y documentación (design D1, D2, D5)

- [x] 3.1 Agregar la pata de macOS a `release.yml` y a `ci.yml`
  - Archivos: `.github/workflows/release.yml`, `.github/workflows/ci.yml`. Depende de: 2.1 y de resolver Q1-Q3.
  - Cambio: una entrada nueva en cada matriz (`smoke` y `wheel`) con el runner de macOS y la versión de Python acordados, con las acciones fijadas por SHA como el resto y los mismos pasos que las patas de Linux. Sin `continue-on-error` ni condiciones.
  - Hecho cuando: las pruebas de 2.1 pasan y `python3 -m unittest tests.test_workflows -v` no tiene fallos.

- [x] 3.2 Documentar la pata de macOS
  - Archivos: `docs/releasing.md`, `docs/testing.md`. Depende de: 3.1.
  - Cambio: agregar la pata a la tabla de patas con su imagen de runner y su Python, las restricciones del runner (sin runtime de Apple ni virtualización; solo comprobaciones sin runtime), y cómo mantener el pin si la imagen se retira.
  - Hecho cuando: la prueba de 2.2 pasa y `docs/` no afirma que macOS valide el nivel `live`.

## 4. Verificación y consecuencias (design D7)

- [x] 4.1 Verificación local completa
  - Archivos: ninguno. Depende de: 3.2.
  - Hecho cuando: `python3 -m unittest discover -s tests` pasa (y con `/usr/bin/python3`), `python3 -m compileall -q src scripts tests` y `git diff --check` están limpios, `git status` no muestra cambios en `docs/evidence/` y `openspec validate validate-wheel-on-macos --strict` pasa.

- [ ] 4.2 Confirmar las patas de macOS en GitHub Actions @needs-human
  - Archivos: ninguno. Depende de: 4.1 y de que el cambio esté en una rama o PR.
  - Por qué una persona: las patas de macOS solo corren en runners alojados de GitHub; no se pueden reproducir localmente.
  - Hecho cuando: el job `wheel` de `ci.yml` pasa en macOS y un tag de pre-release de prueba muestra el job `smoke` de macOS verde antes de `release`.

- [x] 4.3 Generar evidencia en vivo nueva antes del primer tag final @needs-human
  - Archivos: `docs/evidence/runtime-1.5.0-<timestamp>.json` (nuevo). Depende de: 4.2 e integrar este change junto con `reject-reserved-compose-labels` y `fix-empty-service-networks-default`.
  - Por qué una persona: la corrida usa la máquina del mantenedor y la decisión de confirmar el archivo. No es necesaria para pre-releases.
  - Hecho cuando: `git status --porcelain -- src scripts tests pyproject.toml` no imprime nada, `python3 scripts/validate-runtime.py --evidence` termina en 0 con todas las pruebas declaradas aprobadas y `python3 scripts/verify_release_evidence.py --tag v9.9.9` termina en 0. Los archivos de evidencia anteriores no se modifican.
