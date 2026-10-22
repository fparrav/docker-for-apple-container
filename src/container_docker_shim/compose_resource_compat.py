"""Pure compatibility verdicts for Compose resource declarations.

Decides what a top-level volume or network declaration, and a service's
``network_mode``, mean for Apple ``container``: the local bind idiom is
translated, every other ``driver_opts`` form is refused with the offending
option named, and ``network_mode`` gets an actionable message per value
family. Nothing in this module calls the runtime or touches the filesystem,
so the gate (:mod:`.compose_capabilities`) and the translator
(:mod:`.compose`) share one verdict and cannot diverge (design D1, D6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class LocalBind:
    """A top-level volume that Docker expresses as a bind mount of a host path.

    ``device`` is the host path to mount; ``read_only`` comes from ``ro`` in
    ``o``.
    """

    device: str
    read_only: bool


@dataclass(frozen=True)
class Problem:
    """One reason a declaration cannot be honoured, naming the option at fault."""

    message: str


class _NotABind:
    """Type of the :data:`NOT_A_BIND` sentinel."""

    def __repr__(self) -> str:
        return "NOT_A_BIND"


#: A volume that is a plain named volume: not a local bind, and no problem.
#:
#: A volume without ``driver_opts`` (and without a special driver) stays
#: managed by the shim exactly as before, so the callers must not treat it
#: as a refusal; the sentinel keeps that third state distinct from
#: :class:`Problem`.
NOT_A_BIND = _NotABind()

# The only ``o`` tokens the local bind idiom accepts, and the only keys
# ``driver_opts`` may carry; anything else is a Problem, never a guess.
# ``rbind`` is refused with a dedicated message pointing at ``bind``
# (design D5), so it is not part of the accepted set.
_O_BIND = "bind"
_O_RBIND = "rbind"
_O_RO = "ro"
_O_RW = "rw"
_O_TOKENS = frozenset({_O_BIND, _O_RO, _O_RW})
_DRIVER_OPT_KEYS = frozenset({"type", "o", "device"})

# The one network option Apple container cannot apply: it only names a Linux
# bridge, which the runtime does not create.
BRIDGE_NAME_OPTION = "com.docker.network.bridge.name"


def local_bind(volume_spec: Any) -> LocalBind | Problem | _NotABind:
    """Decide whether a top-level volume is the local bind idiom.

    ``volume_spec`` is the normalized top-level volume mapping (``driver``,
    ``driver_opts``, ...). Returns:

    * a :class:`LocalBind` for the idiom: ``driver`` absent or ``local``,
      ``driver_opts`` with ``type: none``, ``o`` made only of ``bind``,
      ``ro`` and ``rw`` with ``bind`` present and an absolute ``device``;
    * a :class:`Problem` naming the offending option for anything else that
      declares ``driver_opts`` or a non-local driver;
    * :data:`NOT_A_BIND` for a volume without ``driver_opts`` and no special
      driver: a plain named volume the shim keeps managing, neither
      translated nor refused.
    """
    if not isinstance(volume_spec, Mapping):
        return NOT_A_BIND
    driver = volume_spec.get("driver")
    if driver is not None and driver != "local":
        return Problem(
            f"volume driver {str(driver)!r} is not supported; only 'local' "
            "(or no driver) can be translated"
        )
    opts = volume_spec.get("driver_opts")
    if opts is None or (isinstance(opts, Mapping) and not opts):
        return NOT_A_BIND
    if not isinstance(opts, Mapping):
        return Problem(
            f"driver_opts must be a mapping of options, got {type(opts).__name__}"
        )
    volume_type = opts.get("type")
    if volume_type is None:
        return Problem("driver_opts type must be 'none' for the local bind idiom")
    if volume_type != "none":
        return Problem(
            f"driver_opts type {str(volume_type)!r} is not supported; the local "
            "bind idiom requires type: none"
        )
    o = opts.get("o")
    if o is None:
        return Problem("driver_opts o is required for the local bind idiom (e.g. o: bind)")
    if not isinstance(o, str):
        return Problem(
            f"driver_opts o must be a comma-separated string, got {type(o).__name__}"
        )
    tokens = [token.strip() for token in o.split(",") if token.strip()]
    if _O_RBIND in tokens:
        return Problem(
            f"driver_opts o token {_O_RBIND!r} is not supported; use {_O_BIND!r}"
        )
    for token in tokens:
        if token not in _O_TOKENS:
            return Problem(
                f"driver_opts o token {token!r} is not supported; only bind, "
                "ro and rw are accepted"
            )
    if _O_RO in tokens and _O_RW in tokens:
        return Problem("driver_opts o cannot contain both ro and rw")
    if _O_BIND not in tokens:
        return Problem(f"driver_opts o {o!r} must contain 'bind'")
    device = opts.get("device")
    if device is None:
        return Problem("driver_opts device is required for the local bind idiom")
    if not isinstance(device, str) or not device.startswith("/"):
        return Problem(f"driver_opts device {str(device)!r} must be an absolute path")
    for key in opts:
        if key not in _DRIVER_OPT_KEYS:
            return Problem(
                f"driver_opts key {str(key)!r} is not supported; only type, o "
                "and device are accepted"
            )
    return LocalBind(
        device=device,
        read_only=_O_RO in tokens,
    )


def classify_network_options(opts: Any) -> tuple[list[str], list[str]]:
    """Split a network's ``driver_opts`` into ``(not_applied, refused)`` messages.

    ``com.docker.network.bridge.name`` is reported as not applied (it only
    names a Linux bridge); every other key is refused with a message naming
    the key. ``None`` or an empty mapping yields two empty lists.
    """
    if opts is None:
        return [], []
    if not isinstance(opts, Mapping):
        return (
            [],
            [
                f"driver_opts must be a mapping of options, got {type(opts).__name__}"
            ],
        )
    not_applied: list[str] = []
    refused: list[str] = []
    for key in opts:
        name = str(key)
        if name == BRIDGE_NAME_OPTION:
            not_applied.append(
                f"{name} only names a Linux bridge, which Apple container does "
                "not create; the option is not applied"
            )
        else:
            refused.append(f"network option {name!r} is not supported by Apple container")
    return not_applied, refused


def network_mode_message(value: str) -> str:
    """One actionable refusal message per ``network_mode`` value family.

    Every value is refused. Apple container has no host network, cannot
    disable a service's networking, and cannot attach a service to another
    service's or container's network namespace; every service runs on the
    project network. Every message contains the literal value so the user can find
    it in the compose file.
    """
    if value == "host":
        return (
            f"network_mode: {value} is not supported: Apple container has no "
            "host network; publish ports with `ports` instead and reach the "
            "host through host.docker.internal"
        )
    if value == "none":
        return (
            f"network_mode: {value} is not supported: Apple container cannot "
            "disable a service's networking, and the shim refuses instead of "
            "giving the service the project network it asked not to have"
        )
    if value.startswith("service:") or value.startswith("container:"):
        return (
            f"network_mode: {value} is not supported: Apple container cannot "
            "attach a service to another service's or container's network; "
            "use the project network, where service names resolve by DNS"
        )
    if value in ("bridge", "default"):
        return (
            f"network_mode: {value} is not supported: Apple container has no "
            "separate default bridge; remove network_mode and the service "
            "joins the project network"
        )
    return (
        f"network_mode: {value} is not supported by Apple container; only the "
        "project network is available"
    )
