# Findings

Runtime: Apple `container` 1.5.0, image `docker.io/library/alpine:3.20`, all
containers named `shimtest-sec-*` and removed by exact name; `container ls -a`
was empty afterwards.

## 1.1 Effects inside the guest

| Flag | Observed |
| --- | --- |
| (none) | uid 0, `CapEff=00000000a80425fb`, root write works |
| `--cap-drop ALL` | `CapEff=0000000000000000` (root file writes still work through file ownership; use `CapEff` as the probe, not a write) |
| `--read-only` | `touch /rootfile` fails with "Read-only file system" |
| `--user 1000:1000` | `id -u` prints 1000, `CapEff=0` |
| `--tmpfs /tmp` | `tmpfs on /tmp type tmpfs (rw,relatime)` |

## 1.2 Capability names

- Bare names are accepted and normalized by the runtime: `--cap-add NET_ADMIN`
  and `--cap-add CAP_NET_ADMIN` both give `capAdd=['CAP_NET_ADMIN']` and
  `CapEff=0x1000` after `--cap-drop ALL`.
- `--cap-drop NET_RAW` is reported as `CAP_NET_RAW` and clears that bit.
- An invalid name (`NOT_A_CAP`) makes `container run` exit 1 with
  `invalid CapabilityName 'NOT_A_CAP'` and no container is left behind.

Consequence: the shim needs no capability normalization or name list. It only
needs to keep forwarding the value and let the runtime refuse invalid names.

## 1.3 Tmpfs options

The runtime passes everything after the first `:` to the guest `mount` as
data, so the options are **applied**:

| Flag | Observed mount |
| --- | --- |
| `/tmp:ro,noexec,size=64m` | `tmpfs (ro,noexec,relatime,size=65536k)` |
| `/tmp:rw,nosuid,nodev,exec,size=512m,mode=1777` | `(rw,nosuid,nodev,relatime,size=524288k)`, mode 1777 |
| `/tmp:size=10g` | `size=10485760k` |
| `/tmp:uid=1000` | `uid=1000` |
| `/tmp:bogus` | fails to start (`mount failed with errno 22`), no container left |

Consequence: the shim's `_tmpfs_path`, which discards the options, is a defect
that silently drops restrictions. The fix is to forward the value verbatim, and
`--tmpfs` becomes SUPPORTED, not PARTIAL. The `tmpfs-options` opt-in is not
needed.

## Pre-existing defect found and fixed during 3.1a

`_parse_run_options` looped forever on any value option written as `--opt=value`
(`--tmpfs=/tmp`, `--memory=1g`, `--name=x`, ...): the branches used
`value = inline_value` and `continue` without advancing `i`. Verified with a 5 s
timeout. Fixed in this change (needed by task 3.3b, which tests `--tmpfs=/run:size=1m`)
by calling `_take_value` directly, which already handles the `--opt=value` form.

## Compose `ulimits` mapping form (found in 4.2a)

Compose defines `ulimits` as a mapping (`nproc: 65535`, `nofile: {soft, hard}`).
`_build_run_args` iterated it as a list, so the mapping form would have emitted
`--ulimit "{'nproc': 65535}"`. Added `_ulimit_values` in `compose.py`, which
produces `<type>=<soft>[:<hard>]`; the list form of strings keeps working.

## 5.2 Live test run (2026-10-18)

`SHIM_TIERS=live python3 -m unittest tests.test_live_security -v` against Apple
`container` 1.5.0 with the real shim, image `alpine:3.20`: 5 tests, all passed
(3.6 s): `--cap-drop ALL` gives `CapEff` all zeros, `--tmpfs /tmp:ro,noexec,size=64m`
shows `ro`, `noexec`, `size=65536k`, `--read-only` rejects a root write,
`--user 1000:1000` prints uid 1000, and `--privileged` exits 64 with no
container created. `container ls -a` showed no `shimtest-sec-` container
afterwards. Not run live: Compose enforcement (covered by unit tests only).
