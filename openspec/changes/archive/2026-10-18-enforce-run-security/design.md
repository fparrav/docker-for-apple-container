# Design

## Context

Run options are translated in `cli.py` (a hand-written flag loop shared by
`run` and `create`), and Compose services are translated in
`compose.py:_build_run_args`. Both paths decide independently what to do with
security options, which is how the CLI came to accept `--security-opt` silently
while Compose warns. Apple `container` 1.5.0 `run --help` documents these
controls: `--cap-add`, `--cap-drop`, `--read-only`, `--ulimit`, `--tmpfs` (path
only), `--user/--uid/--gid`, `--memory`, `--cpus`, `--init`, `--shm-size`, and
the experimental `--masked-path` and `--read-only-path`. It documents no
equivalent for `--security-opt`, `--pids-limit`, `--privileged`, `--hostname`,
`--add-host`, `--restart` or health options.

## Goals / Non-Goals

**Goals:**
- One classification table consumed by both translators.
- No restriction is dropped silently; failures happen before a container exists.
- Supported options are proven to take effect, not only to be forwarded.

**Non-Goals:**
- Emulating missing controls inside the guest (entrypoint wrappers that set
  `ulimit -u`, seccomp profiles, AppArmor).
- Changing options unrelated to restriction (labels, env, ports).

## Decisions

**A single table, not scattered branches.** A module-level mapping from option
to `(status, reason, apple_flag)` is read by the CLI loop and by Compose. A test
compares it with the README table, so documentation and behaviour cannot drift.
Alternative: keep per-translator logic; rejected because that is the current
inconsistency.

**Refuse by default, opt in per option.** Hardened third-party Compose files
commonly carry `security_opt: [no-new-privileges:true]`. Refusing outright
blocks them; ignoring them breaks the maintainer's rule. The shim reads
`CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED`, a comma-separated list of option names
(for example `security-opt,pids-limit`), skips only those, and prints a warning
on every use. There is no wildcard value, so enabling it is a visible, deliberate
choice. Alternative: a global `--force` flag; rejected as too coarse.
**This is an open decision for the maintainer**: if the opt-in is unwanted, the
default behaviour is unchanged and the requirement can be dropped.

**Fail before the runtime is called.** Classification runs while parsing flags,
before the first `container` invocation, so a refusal never leaves a stopped
container behind. For Compose, all services are validated before the first
`container run`, so `up` is all-or-nothing with respect to option support.

**Verify with `inspect` plus a live probe.** Each SUPPORTED option gets a unit
test on the forwarded argv, a check that `docker inspect` reports it, and a live
test (opt-in tier, see `compat-matrix-and-tests`) that exercises the effect:
a dropped capability makes a privileged operation fail, `--read-only` makes a
write fail, `--user` changes `id -u`.

**Capability names.** Spike 1.2 showed that the runtime accepts bare Docker
names, normalizes them to `CAP_*`, and refuses invalid names before creating the
container. The shim therefore forwards the value unchanged and only tests it.

**Tmpfs.** Spike 1.3 showed that the runtime applies the full option string
(`ro`, `noexec`, `nosuid`, `nodev`, `size=`, `mode=`, `uid=`) and refuses invalid
ones. The shim's current behaviour of discarding the options is a defect; the fix
is to forward the value verbatim, and `--tmpfs` is SUPPORTED.

## Classification (initial, subject to the spikes)

| Option / key | Status | Notes |
| --- | --- | --- |
| `--cap-add`, `--cap-drop`, `cap_add`, `cap_drop` | SUPPORTED | names normalized by the runtime (spike 1.2) |
| `--read-only`, `read_only` | SUPPORTED | verified by a failing write |
| `--user`, `user` | SUPPORTED | |
| `--memory`, `--cpus`, `mem_limit`, `cpus`, `deploy.resources.limits` | SUPPORTED | granularity of 1 MiB |
| `--ulimit`, `ulimits` | SUPPORTED | forwarded as `--ulimit` |
| `--tmpfs`, `tmpfs` | SUPPORTED | options applied by the runtime (spike 1.3) |
| `--init`, `init` | SUPPORTED | |
| `--security-opt`, `security_opt` | UNSUPPORTED | |
| `--pids-limit`, `pids_limit` | UNSUPPORTED | |
| `--privileged`, `privileged` | UNSUPPORTED | |
| `--storage-opt` | UNSUPPORTED | |
| `--hostname`, `hostname`; `--add-host`, `extra_hosts` | UNSUPPORTED | not a restriction, already refused |
| `--network none` | UNSUPPORTED | already refused |
| `sysctls`, `devices` | UNSUPPORTED | |

## Risks / Trade-offs

- [Refusing breaks scripts that worked by luck] → The message names the option
  and the opt-in variable, and the README documents the change.
- [`--masked-path` and `--read-only-path` are experimental] → They are not
  exposed; Docker has no direct flags for them.
- [Classification table drifts from the runtime on the next release] → The
  runtime-compatibility process re-runs the live probes for every SUPPORTED row.
- [The legacy Hermes consumer test passes `--security-opt` and `--pids-limit`]
  → Hermes is inherited from the upstream repository and is not a project
  consumer; its contract test sets the opt-in explicitly and can be deleted.
  The agents that matter here are Claude Code, Codex and Pi.
