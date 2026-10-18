from __future__ import annotations

import os
from dataclasses import dataclass


SUPPORTED = "SUPPORTED"
PARTIAL = "PARTIAL"
UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class OptionRule:
    name: str
    status: str
    reason: str
    apple_flag: str | None


RULES: dict[str, OptionRule] = {
    rule.name: rule
    for rule in (
        OptionRule(
            "cap-add",
            SUPPORTED,
            "names normalized to CAP_* by the runtime",
            "--cap-add",
        ),
        OptionRule(
            "cap-drop",
            SUPPORTED,
            "names normalized to CAP_* by the runtime",
            "--cap-drop",
        ),
        OptionRule(
            "read-only",
            SUPPORTED,
            "verified by a failing write",
            "--read-only",
        ),
        OptionRule("user", SUPPORTED, "forwarded unchanged", "--user"),
        OptionRule(
            "memory",
            SUPPORTED,
            "granularity of 1 MiB",
            "--memory",
        ),
        OptionRule(
            "cpus",
            SUPPORTED,
            "forwarded as a fractional CPU count",
            "--cpus",
        ),
        OptionRule(
            "ulimit",
            SUPPORTED,
            "forwarded as --ulimit",
            "--ulimit",
        ),
        OptionRule(
            "tmpfs",
            SUPPORTED,
            "options applied by the runtime",
            "--tmpfs",
        ),
        OptionRule("init", SUPPORTED, "forwarded unchanged", "--init"),
        OptionRule(
            "security-opt",
            UNSUPPORTED,
            "Apple container 1.5.0 has no equivalent: no seccomp/AppArmor/"
            "no-new-privileges knob is documented",
            None,
        ),
        OptionRule(
            "pids-limit",
            UNSUPPORTED,
            "Apple container 1.5.0 documents no process-count limit",
            None,
        ),
        OptionRule(
            "privileged",
            UNSUPPORTED,
            "Apple container 1.5.0 has no privileged mode",
            None,
        ),
        OptionRule(
            "storage-opt",
            UNSUPPORTED,
            "Apple container 1.5.0 documents no storage driver options",
            None,
        ),
        OptionRule(
            "sysctls",
            UNSUPPORTED,
            "Apple container 1.5.0 documents no sysctl passthrough",
            None,
        ),
        OptionRule(
            "devices",
            UNSUPPORTED,
            "Apple container 1.5.0 documents no host device passthrough",
            None,
        ),
        OptionRule(
            "hostname",
            UNSUPPORTED,
            "not a restriction; Apple container 1.5.0 documents no hostname flag",
            None,
        ),
        OptionRule(
            "add-host",
            UNSUPPORTED,
            "not a restriction; Apple container 1.5.0 documents no add-host flag",
            None,
        ),
        OptionRule(
            "network-none",
            UNSUPPORTED,
            "not a restriction; Apple container 1.5.0 has no none network",
            None,
        ),
    )
}


def lookup(name: str) -> OptionRule | None:
    """Return the rule for ``name``, with or without leading dashes."""
    return RULES.get(name.lstrip("-"))


ENV_ALLOW = "CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED"


def unenforced_allowed(environ=None) -> frozenset[str]:
    """Names explicitly allowed to run unenforced, from ``ENV_ALLOW``.

    Comma-separated, whitespace-trimmed, empty entries dropped. There is no
    wildcard: a ``"*"`` value is just another name and grants nothing else.
    """
    if environ is None:
        environ = os.environ
    raw = environ.get(ENV_ALLOW, "") or ""
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Decision:
    action: str
    message: str


def decide(name: str, environ=None) -> Decision:
    """Whether ``name`` can be applied, skipped with a warning, or must be refused."""
    key = name.lstrip("-")
    rule = RULES.get(key)
    if rule is None or rule.status != UNSUPPORTED:
        return Decision("ok", "")
    if key in unenforced_allowed(environ):
        return Decision(
            "skip",
            "warning: --%s is not supported by Apple container (%s) and was "
            "NOT applied (allowed by %s)" % (key, rule.reason, ENV_ALLOW),
        )
    return Decision(
        "refuse",
        "--%s is not supported by Apple container: %s. Set %s=%s to run "
        "without it; the restriction will NOT be enforced."
        % (key, rule.reason, ENV_ALLOW, key),
    )
