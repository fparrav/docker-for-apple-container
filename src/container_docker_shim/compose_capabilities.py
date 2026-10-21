"""Capability validation for a normalized Compose project.

``validate`` is a pure function: it inspects the normalized model of the
services about to be created and returns every refusal and every partial
application, without touching the runtime or the filesystem. The tables below
decide what a Compose key means for this shim; mount modes and attributes are
classified by the shared policy in :mod:`.mounts` (design D4), and the
top-level ``driver_opts`` and ``network_mode`` verdicts come from the shared
pure module :mod:`.compose_resource_compat` (design D1, D6), so the gate and
the translator cannot diverge.

Classification:

* ``SUPPORTED``  - applied as requested (or no runtime meaning, ``inform``).
* ``PARTIAL``    - applied only as far as Apple ``container`` allows; always
  reported, never refused (effect ``behaviour``).
* ``UNSUPPORTED`` - no equivalent. Refused when omitting it would make the
  container more permissive or lose data (effect ``restrict``), unless the user
  names the key in ``CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED`` and the rule is
  waivable. Isolation requests (``network_mode``) are never waivable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from . import compose_resource_compat
from . import mounts
from . import security


SUPPORTED = security.SUPPORTED
PARTIAL = security.PARTIAL
UNSUPPORTED = security.UNSUPPORTED

RESTRICT = "restrict"
BEHAVIOUR = "behaviour"
INFORM = "inform"


def refused(message: str) -> str:
    """One-line refusal marker for standard error: ``REFUSED: <message>``."""
    return "REFUSED: " + message


def not_applied(message: str) -> str:
    """One-line partial-application marker for standard error: ``NOT APPLIED: <message>``."""
    return "NOT APPLIED: " + message


@dataclass(frozen=True)
class Rule:
    status: str
    effect: str
    waivable: bool
    reason: str


def _ok(reason: str = "applied") -> Rule:
    return Rule(SUPPORTED, INFORM, False, reason)


def _part(reason: str) -> Rule:
    return Rule(PARTIAL, BEHAVIOUR, False, reason)


def _no(reason: str, waivable: bool = True) -> Rule:
    return Rule(UNSUPPORTED, RESTRICT, waivable, reason)


NO_EFFECT = "no runtime effect"
_NO_EQUIVALENT = "Apple container 1.5.0 has no equivalent"

# Service keys that route through ``security.decide`` (name of the security rule).
SECURITY_KEYS = {
    "privileged": "privileged",
    "security_opt": "security-opt",
    "pids_limit": "pids-limit",
    "sysctls": "sysctls",
    "devices": "devices",
}

SERVICE_KEYS: dict[str, Rule] = {
    # Applied.
    **{
        key: _ok()
        for key in (
            "build", "cap_add", "cap_drop", "command", "container_name", "cpus",
            "depends_on", "dns", "dns_opt", "dns_search", "entrypoint",
            "env_file", "environment", "healthcheck", "image", "init", "labels",
            "mem_limit", "networks", "platform", "ports", "read_only",
            "shm_size", "stdin_open", "tmpfs", "tty", "ulimits", "user",
            "volumes", "working_dir", "deploy",
        )
    },
    # No runtime meaning for this shim.
    **{
        key: _ok(NO_EFFECT)
        for key in ("expose", "profiles", "annotations", "attach", "develop", "extends", "name")
    },
    # Security keys: the decision comes from ``security.decide``.
    **{key: _no("see run-security-options") for key in SECURITY_KEYS},
    # Behaviour reductions: applied as far as Apple allows, always reported.
    "restart": _part("restart policies are not supported by Apple container"),
    "hostname": _part("custom hostname is not supported by Apple container"),
    "domainname": _part("domainname is not supported by Apple container"),
    "extra_hosts": _part("extra_hosts is not translated"),
    "stop_signal": _part("stop_signal is not applied; the runtime default is used"),
    "stop_grace_period": _part("stop_grace_period is not applied; the runtime default is used"),
    "scale": _part("the shim supports one container per service"),
    "pull_policy": _part("pull_policy is not applied"),
    "mac_address": _part("mac_address is not applied"),
    "links": _part("links are not applied; service names resolve through the project network"),
    "external_links": _part("external_links are not applied"),
    "logging": _part("logging drivers are not applied"),
    "post_start": _part("lifecycle hooks are not run"),
    "pre_stop": _part("lifecycle hooks are not run"),
    # Isolation requests: never waivable.
    "network_mode": _no(
        "Apple container 1.5.0 cannot share, disable or replace the project network; "
        "the service would otherwise gain connectivity it did not request",
        waivable=False,
    ),
    # Secrets and configs: Compose features the shim cannot yet honour.
    "secrets": _no(
        "Compose secrets are not supported yet: Apple container 1.5.0 cannot mount a "
        "single file and the shim will not expose a secret any other way",
        waivable=False,
    ),
    "configs": _no(
        "Compose configs are not supported yet: Apple container 1.5.0 cannot mount a "
        "single file",
        waivable=False,
    ),
    # Restrictions without an equivalent: refused unless waived by name.
    **{
        key: _no(_NO_EQUIVALENT)
        for key in (
            "pid", "ipc", "uts", "cgroup", "cgroup_parent", "userns_mode",
            "cpu_shares", "cpu_quota", "cpu_period", "cpu_rt_period",
            "cpu_rt_runtime", "cpuset", "cpu_count", "cpu_percent",
            "mem_reservation", "memswap_limit", "mem_swappiness",
            "oom_kill_disable", "oom_score_adj", "group_add",
            "device_cgroup_rules", "runtime", "gpus", "volumes_from",
            "blkio_config", "isolation", "storage_opt", "credential_spec",
            "use_api_socket", "provider", "models",
        )
    },
}

BUILD_KEYS: dict[str, Rule] = {
    **{
        key: _ok()
        for key in (
            "context", "dockerfile", "args", "target", "labels", "platforms",
            "no_cache", "pull", "secrets", "ssh",
        )
    },
    **{
        key: _no("not forwarded to the Apple container build")
        for key in (
            "cache_from", "cache_to", "additional_contexts", "network",
            "extra_hosts", "isolation", "privileged", "shm_size", "tags",
            "ulimits", "entitlements", "dockerfile_inline", "provenance", "sbom",
        )
    },
}

# The `type` values a long-syntax entry may carry; every other value is
# classified by the mounts policy.
MOUNT_TYPES = frozenset({"bind", "volume"})

DEPLOY_KEYS: dict[str, Rule] = {
    "resources": _ok(),
    "replicas": _part("the shim supports one container per service"),
    "restart_policy": _part("restart policies are not supported by Apple container"),
    **{
        key: _ok(NO_EFFECT)
        for key in ("mode", "labels", "update_config", "rollback_config", "placement", "endpoint_mode")
    },
}

# Top-level network and volume attributes, per resource kind.
RESOURCE_ATTRIBUTES: dict[str, dict[str, Rule]] = {
    "networks": {
        **{key: _ok() for key in ("name", "labels", "internal", "external")},
        **{
            key: _no("changes how the network behaves and has no Apple equivalent", False)
            for key in ("driver_opts", "ipam", "enable_ipv6", "enable_ipv4", "attachable")
        },
        "driver": _ok(),
    },
    "volumes": {
        **{key: _ok() for key in ("name", "labels", "external")},
        "driver_opts": _no("changes where the data lives and has no Apple equivalent", False),
        "driver": _ok(),
    },
}
_DEFAULT_DRIVERS = {"networks": ("bridge", "default"), "volumes": ("local",)}

# Values that mean "nothing was requested" for a PARTIAL key.
_DEFAULT_VALUES: dict[str, tuple] = {
    "restart": ("no", False),
    "scale": (1, "1"),
    "stop_signal": ("SIGTERM", "TERM"),
    "pull_policy": ("missing", "if_not_present"),
    "deploy.replicas": (1, "1"),
}


@dataclass(frozen=True)
class Finding:
    """One refusal or one partial application, naming where it applies."""

    service: str
    key: str
    message: str


@dataclass
class Report:
    refusals: list[Finding] = field(default_factory=list)
    notices: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refusals


def requested(value: Any) -> bool:
    """Whether a value counts as requested (not an empty or no-op form)."""
    if value is None or value is False:
        return False
    return value not in ("", [], {}, "false", "False", "0", "-1", 0, -1)


def validate(
    project: Mapping[str, Any] | None,
    services: Iterable[Any],
    environ: Mapping[str, str] | None = None,
) -> Report:
    """Classify every key of ``services`` and the resources they use.

    ``services`` are objects with ``name`` and ``spec`` (the ``Service`` class
    of ``compose.py``) or ``(name, spec)`` pairs. ``project`` is the normalized
    model whose top-level ``networks`` and ``volumes`` are checked for the
    resources the services reference. Nothing is read from disk.
    """
    if environ is None:
        environ = os.environ
    allowed = security.unenforced_allowed(environ)
    report = Report()
    used_networks: dict[str, str] = {}
    used_volumes: dict[str, str] = {}
    for service in services:
        name, spec = _name_and_spec(service)
        _service(name, spec, allowed, environ, report)
        _collect_resources(name, spec, used_networks, used_volumes)
    _resources(project or {}, "networks", used_networks, report)
    _resources(project or {}, "volumes", used_volumes, report)
    return report


def _name_and_spec(service: Any) -> tuple[str, Mapping[str, Any]]:
    if isinstance(service, tuple):
        return service[0], service[1]
    return service.name, service.spec


def _refuse(report: Report, service: str, key: str, text: str) -> None:
    report.refusals.append(Finding(service, key, text))


def _notice(report: Report, service: str, key: str, text: str) -> None:
    report.notices.append(Finding(service, key, text))


def _apply_rule(
    rule: Rule,
    name: str,
    key: str,
    value: Any,
    allowed: frozenset[str],
    report: Report,
    waiver_names: Iterable[str] = (),
) -> None:
    if rule.status == SUPPORTED:
        return
    if rule.status == PARTIAL:
        if value in _DEFAULT_VALUES.get(key, ()) or not requested(value):
            return
        _notice(report, name, key, rule.reason)
        return
    if not requested(value):
        return
    if not rule.waivable:
        _refuse(
            report, name, key,
            "%s. Isolation requests cannot be waived, even when named in %s."
            % (rule.reason, security.ENV_ALLOW),
        )
    elif key in allowed or any(w in allowed for w in waiver_names):
        _notice(report, name, key, "%s (waived by %s)" % (rule.reason, security.ENV_ALLOW))
    else:
        _refuse(
            report, name, key,
            "%s; to run without it, list '%s' in %s" % (rule.reason, key, security.ENV_ALLOW),
        )


def _service(
    name: str,
    spec: Mapping[str, Any],
    allowed: frozenset[str],
    environ: Mapping[str, str],
    report: Report,
) -> None:
    for key, value in spec.items():
        if key.startswith("x-"):
            continue
        rule = SERVICE_KEYS.get(key)
        if rule is None:
            _refuse(report, name, key, "unknown service key '%s'; the shim does not classify it" % key)
            continue
        if key in SECURITY_KEYS:
            if requested(value):
                decision = security.decide(SECURITY_KEYS[key], environ)
                if decision.action == "refuse":
                    _refuse(report, name, key, decision.message)
                elif decision.action == "skip":
                    _notice(report, name, key, decision.message)
            continue
        if key == "build":
            _build(name, value, report)
        elif key == "deploy":
            _deploy(name, value, allowed, environ, report)
        elif key == "networks":
            _service_networks(name, value, report)
        elif key == "volumes":
            _service_volumes(name, value, report)
        elif key == "network_mode":
            _network_mode(name, value, report)
        else:
            _apply_rule(rule, name, key, value, allowed, report)


def _network_mode(name: str, value: Any, report: Report) -> None:
    """The isolation refusal: never waivable, with a specific message per value.

    A non-empty string value gets the message from the shared pure module
    (one per value family); any other value keeps the generic reason.
    """
    if not requested(value):
        return
    rule = SERVICE_KEYS["network_mode"]
    # The report line already starts with the key, so the message's own
    # "network_mode: " lead-in is dropped to avoid saying it twice.
    body = (
        compose_resource_compat.network_mode_message(value).removeprefix("network_mode: ")
        if isinstance(value, str)
        else rule.reason
    )
    _refuse(
        report, name, "network_mode",
        "%s. Isolation requests cannot be waived, even when named in %s."
        % (body, security.ENV_ALLOW),
    )


def _build(name: str, value: Any, report: Report) -> None:
    if not isinstance(value, Mapping):
        return
    for key, item in value.items():
        rule = BUILD_KEYS.get(key)
        if rule is None:
            _refuse(report, name, "build.%s" % key, "unknown build key '%s'" % key)
        elif rule.status == UNSUPPORTED and requested(item):
            _refuse(report, name, "build.%s" % key, "build.%s: %s" % (key, rule.reason))
        elif key == "ssh" and requested(item) and not _ssh_default_only(item):
            _refuse(report, name, "build.ssh", "only `ssh: default` is forwarded to the build")


def _ssh_default_only(value: Any) -> bool:
    if isinstance(value, str):
        return value == "default"
    if isinstance(value, list):
        return all(item == "default" for item in value)
    if isinstance(value, Mapping):
        return set(value) <= {"default"}
    return False


def _deploy(
    name: str,
    value: Any,
    allowed: frozenset[str],
    environ: Mapping[str, str],
    report: Report,
) -> None:
    if not isinstance(value, Mapping):
        return
    for key, item in value.items():
        rule = DEPLOY_KEYS.get(key)
        if rule is None:
            _refuse(report, name, "deploy.%s" % key, "unknown deploy key '%s'" % key)
            continue
        if key == "resources" and isinstance(item, Mapping):
            _deploy_resources(name, item, allowed, environ, report)
        else:
            _apply_rule(rule, name, "deploy.%s" % key, item, allowed, report)


def _deploy_resources(
    name: str,
    resources: Mapping[str, Any],
    allowed: frozenset[str],
    environ: Mapping[str, str],
    report: Report,
) -> None:
    limits = resources.get("limits")
    if isinstance(limits, Mapping) and requested(limits.get("pids")):
        decision = security.decide("pids-limit", environ)
        if decision.action == "refuse":
            _refuse(report, name, "deploy.resources.limits.pids", decision.message)
        elif decision.action == "skip":
            _notice(report, name, "deploy.resources.limits.pids", decision.message)
    if requested(resources.get("reservations")):
        _notice(report, name, "deploy.resources.reservations", "reservations are not applied")
    if requested(resources.get("devices")):
        decision = security.decide("devices", environ)
        if decision.action == "refuse":
            _refuse(report, name, "deploy.resources.devices", decision.message)
        elif decision.action == "skip":
            _notice(report, name, "deploy.resources.devices", decision.message)


def _service_networks(name: str, value: Any, report: Report) -> None:
    if not isinstance(value, Mapping):
        return
    seen: set[str] = set()
    for config in value.values():
        if not isinstance(config, Mapping):
            continue
        for attr, item in config.items():
            key = "networks.%s" % attr
            if requested(item) and key not in seen:
                seen.add(key)
                text = (
                    "network aliases are not applied; service-name discovery remains available"
                    if attr == "aliases"
                    else "per-network %s is not applied" % attr
                )
                _notice(report, name, key, text)


def _mount_mode(name: str, option: str, report: Report, target: str) -> None:
    """One short-syntax mode flag, classified by the shared mounts policy."""
    try:
        mounts.check_short_modes(option, target)
    except mounts.MountSpecError as exc:
        _refuse(report, name, "volumes.%s" % option, str(exc))


def _mount_attribute(name: str, option: str, value: Any, report: Report, target: str) -> None:
    """One long-syntax attribute (or `type` value), classified by the mounts policy."""
    try:
        mounts.attribute_rule(option, value, target)
    except mounts.MountSpecError as exc:
        _refuse(report, name, "volumes.%s" % option.rsplit(".", 1)[-1], str(exc))


def _mount_type(name: str, value: Any, report: Report, target: str) -> None:
    kind = "" if value is None else str(value)
    if not kind or kind in MOUNT_TYPES:
        return
    _mount_attribute(name, kind, value, report, target)


def _service_volumes(name: str, value: Any, report: Report) -> None:
    if not isinstance(value, list):
        return
    for entry in value:
        if isinstance(entry, Mapping):
            target = str(entry.get("target") or "")
            for option, item in entry.items():
                if option in mounts.LONG_SECTIONS:
                    if isinstance(item, Mapping):
                        for sub, subvalue in item.items():
                            _mount_attribute(name, "%s.%s" % (option, sub), subvalue, report, target)
                    continue
                if option == "type":
                    _mount_type(name, item, report, target)
                    continue
                if option in mounts.STRUCTURAL_KEYS:
                    continue
                _mount_attribute(name, option, item, report, target)
        elif isinstance(entry, str):
            parts = entry.split(":")
            if len(parts) > 2:
                for option in parts[2].split(","):
                    if option:
                        _mount_mode(name, option, report, target=parts[1])


def _collect_resources(
    name: str,
    spec: Mapping[str, Any],
    networks: dict[str, str],
    volumes: dict[str, str],
) -> None:
    declared = spec.get("networks")
    if isinstance(declared, (list, Mapping)):
        for net in declared:
            networks.setdefault(str(net), name)
    else:
        networks.setdefault("default", name)
    for entry in spec.get("volumes") or []:
        if isinstance(entry, Mapping):
            if entry.get("type", "volume") == "volume" and entry.get("source"):
                volumes.setdefault(str(entry["source"]), name)
        elif isinstance(entry, str):
            source = entry.split(":")[0]
            if source and not source.startswith((".", "/", "~")) and "/" not in source:
                volumes.setdefault(source, name)


def _resources(
    project: Mapping[str, Any], kind: str, used: Mapping[str, str], report: Report
) -> None:
    declared = project.get(kind)
    if not isinstance(declared, Mapping):
        return
    singular = kind[:-1]
    for key, owner in used.items():
        attrs = declared.get(key)
        if not isinstance(attrs, Mapping):
            continue
        subject = "%s %s" % (singular, key)
        for attr, item in attrs.items():
            rule = RESOURCE_ATTRIBUTES[kind].get(attr)
            if rule is None:
                _refuse(report, owner, "%s.%s" % (kind, attr), "%s: unknown attribute '%s'" % (subject, attr))
            elif attr == "driver":
                if requested(item) and item not in _DEFAULT_DRIVERS[kind]:
                    _refuse(report, owner, "%s.driver" % kind, "%s: driver '%s' is not supported" % (subject, item))
            elif attr == "driver_opts":
                _driver_opts(kind, key, attrs, owner, report)
            elif rule.status == UNSUPPORTED and requested(item):
                _refuse(report, owner, "%s.%s" % (kind, attr), "%s: %s" % (subject, rule.reason))


def _driver_opts(
    kind: str, key: str, attrs: Mapping[str, Any], owner: str, report: Report
) -> None:
    """The value-sensitive ``driver_opts`` verdict from the shared pure module.

    A volume that is the local bind idiom is applied (no finding); every other
    volume form is refused with the offending option named. A network's bridge
    name is reported as not applied; every other network option is refused and
    named. Neither verdict is waivable.
    """
    item = attrs.get("driver_opts")
    if not requested(item):
        return
    if kind == "volumes":
        driver = attrs.get("driver")
        if requested(driver) and driver not in _DEFAULT_DRIVERS[kind]:
            return  # the driver check above already refused this volume
        verdict = compose_resource_compat.local_bind(attrs)
        if isinstance(verdict, compose_resource_compat.Problem):
            _refuse(report, owner, "volumes.driver_opts", "volume %s: %s" % (key, verdict.message))
        return
    not_applied, refused = compose_resource_compat.classify_network_options(item)
    for message in not_applied:
        _notice(report, owner, "networks.driver_opts", "network %s: %s" % (key, message))
    for message in refused:
        _refuse(report, owner, "networks.driver_opts", "network %s: %s" % (key, message))


def unknown_waivers(environ: Mapping[str, str] | None = None) -> list[str]:
    """Names in the waiver variable that no command can honour.

    A name is known when it is a waivable ``security`` option or a waivable
    Compose service key; everything else (typos, isolation requests) is
    reported so a waiver that grants nothing is never silent.
    """
    compose_keys = {
        key
        for key, rule in SERVICE_KEYS.items()
        if rule.status == UNSUPPORTED and rule.waivable and key not in SECURITY_KEYS
    }
    return [name for name in security.unknown_waivers(environ) if name not in compose_keys]


def all_service_keys() -> frozenset[str]:
    """Every service key the table knows (for the matrix coverage test)."""
    return frozenset(SERVICE_KEYS)
