# Proposal

## Why

A Compose file that asks for network isolation, a read-only mount, a secret or
a build target can run today with weaker semantics than requested and exit 0.
`network_mode: none` joins the project network, a `read_only` named volume is
mounted read-write, `secrets` are dropped, `build.target` is ignored and any
key the shim has never heard of is silently skipped. The project promises that
a restriction is applied or the command fails before anything is created
(`run-security-options`, `compose-normalization`); for Compose that promise is
only kept for five keys. See `findings.md` (2.1 to 2.4, 5.1 to 5.4, 7.1, 9.1 to
9.3) for the evidence, reproduced against the working tree.

## What Changes

- Add one **capability validation phase** that runs on the normalized model
  before `compose up` creates a network, volume, image build or container. It
  classifies every service key, `build` sub-key, volume/mount option and
  top-level resource attribute as SUPPORTED, PARTIAL or UNSUPPORTED and returns
  all refusals at once, each naming service, key and reason.
- **Fail closed on unknown keys**: a key that is not classified is refused
  (Compose `x-` extensions stay ignored, as Compose does).
- Refuse `network_mode` (any value), `secrets`, `configs` and the isolation or
  resource keys with no Apple equivalent (`pid`, `ipc`, `userns_mode`,
  `cpu_shares`, `mem_reservation`, `group_add`, `runtime`, `gpus`, ...).
- Enforce `read_only` and mount options on named volumes, bind mounts and
  tmpfs, in short and long syntax, or refuse the mount. A live write test
  decides whether Apple can honour read-only on a named volume.
- Forward the `build` sub-keys the CLI already supports (`target`, `labels`,
  `no_cache`, `pull`, `secrets`, `ssh: default`) and refuse the rest.
- Make the waiver policy fail closed: **network isolation requests can never be
  waived**; other waivers stay explicit, per option, and every waived or
  partially applied option prints a stable `NOT APPLIED` line on standard
  error. **BREAKING** for Compose files that silently relied on ignored
  `secrets`, `configs`, unknown keys or `network_mode`.
- Record the secrets and configs decision: refuse now (alternative B), keep a
  spike for a read-only file mount (alternative A) as a follow-up change.

## Capabilities

### New Capabilities

- `compose-capability-validation`: the pre-deployment validation phase, the
  classification of Compose keys, the policy for PARTIAL keys, the non-waivable
  isolation requests and the unambiguous reporting of keys that were not
  applied.

### Modified Capabilities

- `compose-normalization`: "Unknown configuration is not interpreted silently"
  becomes a refusal for unclassified keys instead of a warning.
- `compose-volume-safety`: new requirements that read-only and other mount
  options are enforced or refused, and that unsupported mount options are
  never dropped.
- `run-security-options`: new requirements that isolation requests cannot be
  waived and that waived options are reported with a stable token. Existing
  requirements are unchanged.

## Impact

- Code: new `src/container_docker_shim/compose_capabilities.py`;
  `compose.py` (`cmd_up` ordering, `_classify_and_translate_volume`,
  `_run_build`, `_warn_unsupported`, `UNSUPPORTED_SERVICE_KEYS`),
  `security.py` (waiver classes), `compose_normalizer.py` untouched.
- Tests: `tests/test_compose.py`, new `tests/test_compose_capabilities.py`,
  `tests/test_security.py`, `tests/test_option_sets.py`,
  `tests/test_live_fixtures.py` (fixtures J and a new read-only/network-none
  fixture), `tests/compat/matrix.json` (about 60 new `compose` entries).
- Docs: README (Compose and Security sections, regenerated matrix),
  `docs/testing.md`.
- Compatibility: Compose files stay valid for Docker; only the shim becomes
  stricter. Users who want the old behaviour for one key opt in with
  `CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED=<key>`, except for network isolation.
- Out of scope: translating secrets/configs (follow-up after the spike),
  Engine API, new daemons, scaling, network aliases, `compose run`.
- Regression risk: the new refusals touch the common path (`up`). Mitigation:
  every refusal comes from a table with a test per entry, and the existing
  live fixtures A to L must stay green.
- Order: this change lands first. `compose-resource-identity` plugs its
  dependency closure into the same validation input.
