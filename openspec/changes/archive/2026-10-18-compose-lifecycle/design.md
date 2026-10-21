# Design

## Context

`cmd_up` builds a topological order, removes the project's existing
containers, runs `container run -d` for each service, reads each address, and
then injects `/etc/hosts` lines into every container (`_inject_etc_hosts`).
Containers carry Docker's `com.docker.compose.*` labels (project, service,
container-number, oneoff). `UNSUPPORTED_SERVICE_KEYS` lists `healthcheck` as
"not enforced". The shim is stateless by contract: no files, no daemon, no
database. Apple `container` 1.5.0 offers `exec`, `inspect`, `logs`, labels on
containers, networks and volumes, `network create --internal`, and
`system dns` for admin-created DNS domains. It offers no restart policy and no
health state.

## Goals / Non-Goals

**Goals:**
- Dependencies are usable, not just created, before dependents start.
- Re-running `up` converges without touching unchanged services or data.
- Service names resolve, including after restarts.

**Non-Goals:**
- A background supervisor or health monitor (violates the no-resident-process
  rule).
- `State.Health` in `docker inspect`, restart policies, scaling.

## Decisions

**Health is evaluated inside the `up` invocation.** Docker Compose itself only
needs health to decide when to create dependents; it does so during `up`. The
shim does the same: after creating a service that has dependents with
`service_healthy`, it loops `container exec <name> <test>` using the declared
timings and returns when healthy or when the bound is hit. No process outlives
the command. Alternative: a detached watcher that writes health to labels;
rejected because labels are fixed at creation and a watcher is resident.

**Default bounds follow Compose.** Defaults are interval 30s, timeout 30s,
retries 3, start_period 0. The wait limit is `start_period + retries *
(interval + timeout)`, plus a floor, and `--wait-timeout` overrides it. The
lower defaults of fast stacks are left to the file; the shim does not shorten
them. During `start_period` the shim polls at `start_interval` (default 5s)
when set, otherwise at `interval`.

**Exit codes for `service_completed_successfully`.** The shim needs the
exit code of a stopped container from `container inspect`. Task 1.2 confirms the
field exists in 1.5.0. If it does not, the condition is refused (exit 64) rather
than guessed, and the limitation is documented.

**Hash labels for change detection.** `cmd_up` computes a SHA-256 over the
normalized service dictionary (after interpolation, excluding `depends_on`
ordering noise and the build context's file contents), plus the resolved image
ID, and stores them in `com.docker.compose.config-hash` and
`com.docker.compose.image`. These are Docker's own label names, so the format
stays recognizable. On `up`, per service: same hash and image → keep and start
if stopped; different → recreate; missing labels → recreate once. Dependents of
a recreated service are recreated too, because their injected addresses and
conditions may be stale. Alternative: always recreate (today); rejected as
destructive. Alternative: a state file; rejected by the stateless rule.

**Partial failure.** Containers are created in order; on a failure the command
stops, prints the service and the runtime error, and does not remove what
already runs. Convergence on the next `up` replaces the removal step the old
implementation performed up front.

**Volumes.** Volume creation stays idempotent through
`_ensure_named_volume`. Recreation never passes a volume removal. `down -v`
removes only volumes whose project label matches; the check exists in
`_load_labeled_resources` and gets a test that includes an external volume and
another project's volume.

**Service discovery: keep hosts injection, prove it, evaluate native DNS.**
The existing two-layer design (boot-time prelude for dependencies, post-start
exec for peers, `container cp` fallback for shell-less images) already covers
the cases in the spec on paper. The work is to prove it with an API +
PostgreSQL test through restarts, and to run a spike (task 4.1) on what 1.5.0
offers natively: whether containers on one user-defined network resolve each
other by container name without an admin DNS domain, and whether
`--dns-domain` helps. If a native mechanism works without admin rights and
without a global DNS change, adopt it and keep hosts injection only for
`host.docker.internal`. If it needs `sudo container system dns create`, which
alters host resolver configuration, it is rejected by the requirements.

## Risks / Trade-offs

- [Hash churn causes needless recreation] → The hash input is the normalized
  dictionary only; a test pins that reordering keys or whitespace does not
  change it.
- [A health test that needs tools missing from the image] → The failure output
  is shown; `test: NONE` and `service_started` remain available.
- [Recreating dependents surprises users] → The output lists what is recreated
  and why; `--no-recreate` is available.
- [Polling cost] → Bounded by `interval`; no polling after the command returns.
