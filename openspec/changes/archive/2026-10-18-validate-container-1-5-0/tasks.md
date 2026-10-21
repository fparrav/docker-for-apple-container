# Tasks

## 1. Live validation on 1.5.0 (read-only first)

- [x] 1.1 Confirm the runtime with `container system version` and `container system status`, and verify both report 1.5.0 and a running apiserver @worker:orchestrator @review:none
- [x] 1.2 Run the matrix that works on an empty runtime through the shim (version, info, ps -a, images, image inspect on `docker.io/library/postgres:17`) with the constant still at 1.4.1 and `CONTAINER_DOCKER_SHIM_CONTAINER=/opt/homebrew/bin/container`, and record in `findings.md` which commands fail and how. Commands that need a container (inspect, port, logs --tail, compose ps) are covered in 4.2 @worker:orchestrator
- [x] 1.3 Capture live 1.5.0 JSON for ls, inspect, image ls and image inspect, diff their key sets against the parser requirements and test fixtures, and verify the findings are written down in `openspec/changes/validate-container-1-5-0/findings.md`. Include why `docker images` shows `postgres:17` at 10.2kB (index `descriptor.size`) and an image created "56 years ago", and state whether it also reproduces on 1.4.1 @worker:orchestrator

## 2. Fix inconsistencies found

- [x] 2.1 For each finding from 1.2 and 1.3 other than 2.2 and 2.3, update the translation or strict parser in `cli.py` (and `compose.py` if affected) to the 1.5.0 schema, adding or adjusting a unit test per fix and verifying it passes. Re-atomize into one-file sub-tasks once `findings.md` exists @worker:orchestrator
- [x] 2.2a Change `_container_bin()` in `src/container_docker_shim/cli.py` to return `CONTAINER_DOCKER_SHIM_CONTAINER` when set, otherwise `shutil.which("container")`, and otherwise raise `ShimError` whose message names both `CONTAINER_DOCKER_SHIM_CONTAINER` and `PATH`; remove the fixed `/usr/local/bin/container` default. Verify `python3 -m unittest discover -s tests` still passes @worker:local
- [x] 2.2b Add unit tests in `tests/test_cli.py` for override, PATH-only and not-found resolution of the `container` executable, and verify they pass with `python3 -m unittest discover -s tests` @worker:local
- [x] 2.3a Change `_docker_image_list_row` in `src/container_docker_shim/cli.py` so the image list reads size from the selected variant (`_select_image_variant(item, None)`, falling back to `configuration.descriptor.size`) and creation time from the variant `config.created` before `configuration.creationDate`; when the resulting time is missing or not after 1970 set `CreatedAt` to `DOCKER_ZERO_TIME` and `CreatedSince` to `N/A`. Verify `python3 -m unittest discover -s tests` still passes @worker:local
- [x] 2.3b Add a test in `tests/test_image.py` using `update_fake_state(image_payload=...)` with one image whose index `descriptor.size` (10237) differs from its variant `size` (159971863) and one whose `creationDate` is `1970-01-01T00:00:00Z` with no `config.created`, asserting the listed size is `160MB`, `CreatedSince` is `N/A` for the second image, and verify it passes @worker:local
- [x] 2.4 Update fixtures that differ from the captured 1.5.0 shape and verify `python3 -m unittest discover -s tests -v` passes @worker:orchestrator

## 3. Advance the supported release to 1.5.0

- [x] 3.1 Change `SUPPORTED_CONTAINER_VERSION` to 1.5.0 and update the fake-binary echoes and assertions in `test_cli.py`, `test_compose.py`, `test_inspect.py` and `test_hermes_contract.py`; verify the unit suite passes @worker:orchestrator
- [x] 3.2 Update the README requirements text and the development baseline sentence to 1.5.0; verify `grep -rn "1\.4\.1" README.md src tests` returns nothing @worker:orchestrator @review:none
- [x] 3.3 Rewrite the runtime-contract bullet in `AGENTS.md` to reference the code constant instead of a version literal; verify `grep -n "1\.4\.1" AGENTS.md` returns nothing and the one-release, strict-schema and no-shim-state rules are still present @worker:orchestrator @review:none

## 4. Live lifecycle smoke and final checks

- [x] 4.1 With the constant at 1.5.0 and `CONTAINER_DOCKER_SHIM_CONTAINER` unset, rerun the empty-runtime matrix (version, info, ps -a, images, image inspect) and verify every command exits 0 with Docker-shaped output, and that `docker images` shows plausible sizes and dates @worker:orchestrator
- [x] 4.2 On one explicitly named disposable container run run -d with a published port, inspect `.State.Status`, port, logs --tail, exec, compose ps, stop and rm, verify each step succeeds, and verify afterwards that `container ls -a` no longer lists it @worker:orchestrator
- [x] 4.3 Run `openspec validate validate-container-1-5-0 --strict` and verify it passes @worker:orchestrator @review:none

## Workflow follow-up

- Archive the change after the validation evidence is reviewed.
- Decide separately whether to prepare a commit in the English Scoped Commits
  format (`runtime: ⬆️ advance supported runtime to Apple container 1.5.0`),
  keeping `AGENTS.md` out of it.
