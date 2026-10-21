"""Pure Docker volume and mount parsing for Apple ``container run``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping


class MountSpecError(ValueError):
    """A Docker mount option cannot be represented safely by Apple container."""


@dataclass(frozen=True)
class Mount:
    kind: str
    source: str
    target: str
    readonly: bool = False


# One verdict per mount option, shared by `docker run` and Compose (design
# D4); nothing else owns a copy of this policy.
READ_ONLY = "read-only"
NO_EFFECT = "no-effect"
REFUSED = "refused"

# Short-syntax modes: the comma list in `source:target:mode`.
MODE_RULES: dict[str, str] = {
    "ro": READ_ONLY,
    "rw": NO_EFFECT,
    "cached": NO_EFFECT,
    "delegated": NO_EFFECT,
    # Docker's z/Z request SELinux relabeling. Apple container 1.5.0 documents
    # --mount as type/source/target/readonly only and live-rejects the
    # directive ("unknown directive selinux when parsing mount"), so the shim
    # refuses z/Z instead of silently dropping them.
    "z": REFUSED,
    "Z": REFUSED,
}

# Long-syntax attributes, keyed "section.name" (no section: the entry
# itself); `image` and `tmpfs` are refused `type` values.
ATTRIBUTE_RULES: dict[str, str] = {
    "read_only": READ_ONLY,
    "consistency": NO_EFFECT,
    # Observed live (findings.md F5): Docker creates a missing bind source by
    # default, Apple container 1.5.0 refuses to start and creates nothing, so
    # the option is accepted but changes nothing.
    "bind.create_host_path": NO_EFFECT,
    "bind.selinux": REFUSED,
    "bind.propagation": REFUSED,
    "volume.nocopy": REFUSED,
    "volume.subpath": REFUSED,
    "image": REFUSED,
    "tmpfs": REFUSED,
}

# Key names of Docker's comma-list `--mount` dialect.
_LONG_KEYS = frozenset({"type", "source", "src", "target", "dst", "destination", "readonly", "ro"})


def is_host_path(source: str) -> bool:
    """Classify Docker's path-like sources; bare names are named volumes."""
    return source.startswith((".", "/", "~")) or "/" in source


def _resolve_host_path(source: str, cwd: str | None = None) -> str:
    expanded = os.path.expanduser(source)
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    return os.path.normpath(os.path.join(cwd or os.getcwd(), expanded))


def _validate_target(target: str) -> None:
    if not target:
        raise MountSpecError("mount target is required")
    if not target.startswith("/"):
        raise MountSpecError(f"mount target must be an absolute path: {target!r}")
    if "," in target:
        raise MountSpecError(f"mount targets containing commas are unsupported: {target!r}")


def _validate_source(source: str, target: str) -> None:
    if not source:
        raise MountSpecError("mount source is required")
    if "," in source:
        raise MountSpecError(
            f"mount sources containing commas are unsupported: {source!r} on {target!r}"
        )


def _mount(source: str, target: str, readonly: bool, cwd: str | None = None) -> Mount:
    _validate_target(target)
    _validate_source(source, target)
    if is_host_path(source):
        return Mount("bind", _resolve_host_path(source, cwd), target, readonly)
    return Mount("volume", source, target, readonly)


def emit_bind(mount: Mount) -> list[str]:
    return _emit_mount("bind", mount.source, mount.target, mount.readonly)


def emit_volume(mount: Mount) -> list[str]:
    return _emit_mount("volume", mount.source, mount.target, mount.readonly)


def emit_tmpfs(target: str, options: str = "") -> list[str]:
    """Emit the Apple tmpfs spelling while preserving Docker's option suffix."""
    path = f"{target}:{options}" if options else target
    return ["--tmpfs", path]


def _emit_mount(kind: str, source: str, target: str, readonly: bool) -> list[str]:
    spec = f"type={kind},source={source},target={target}"
    if readonly:
        spec += ",readonly"
    return ["--mount", spec]


def emit(mount: Mount) -> list[str]:
    if mount.kind == "bind":
        return emit_bind(mount)
    if mount.kind == "volume":
        return emit_volume(mount)
    if mount.kind == "tmpfs":
        return emit_tmpfs(mount.target)
    raise MountSpecError(f"unsupported mount type: {mount.kind!r}")


def _selinux_refusal(name: str, target: str) -> str:
    """Refusal text for an SELinux directive: names the option and the target."""
    return (
        f"{name} on {target!r} requests SELinux relabeling, which Apple container "
        "cannot represent; remove it because macOS has no SELinux"
    )


def check_short_modes(mode: str, target: str) -> bool:
    """Classify a short-syntax mode list against ``MODE_RULES``.

    Returns the read-only verdict. A refused or unknown mode raises
    ``MountSpecError`` naming the mode and the target.
    """
    readonly = False
    for flag in (item.strip() for item in mode.split(",")):
        if not flag:
            continue
        rule = MODE_RULES.get(flag)
        if rule == READ_ONLY:
            readonly = True
        elif rule == REFUSED:
            raise MountSpecError(_selinux_refusal(f"volume mode {flag!r}", target))
        elif rule is None:
            raise MountSpecError(f"unsupported volume mode {flag!r} on {target!r}")
    return readonly


def parse_volume_spec(value: str, *, cwd: str | None = None) -> Mount:
    """Parse Docker ``-v source:target[:mode]`` into a typed mount."""
    parts = value.split(":", 2)
    if len(parts) < 2:
        raise MountSpecError(f"unsupported volume syntax: {value}")
    source, target = parts[:2]
    mode = parts[2] if len(parts) == 3 else ""
    readonly = check_short_modes(mode, target)
    return _mount(source, target, readonly, cwd)


def parse_volume(value: str, *, cwd: str | None = None) -> list[str]:
    """Parse Docker ``-v`` syntax and emit Apple arguments."""
    return emit(parse_volume_spec(value, cwd=cwd))


# Long-syntax keys that carry structure, not options; every other key must be
# an entry in ``ATTRIBUTE_RULES``.
STRUCTURAL_KEYS = frozenset({"type", "source", "src", "target", "dst", "destination"})
LONG_SECTIONS = ("bind", "volume")


def attribute_rule(name: str, value: Any, target: str) -> str:
    """Classify one long-syntax attribute; a refused or unknown one raises."""
    rule = ATTRIBUTE_RULES.get(name)
    if rule == REFUSED:
        if name == "bind.selinux" and not value:
            return NO_EFFECT
        if name == "bind.selinux":
            raise MountSpecError(_selinux_refusal(f"volume option {name!r}", target))
        raise MountSpecError(f"unsupported volume option {name!r} on {target!r}")
    if rule is None:
        raise MountSpecError(f"unknown volume option {name!r} on {target!r}")
    return rule


def mount_from_entry(
    entry: Mapping[str, Any],
    project_dir: str | None = None,
    volume_name: str | None = None,
) -> Mount:
    """Translate one long-syntax Compose volume entry into a typed mount.

    Every key is checked against ``ATTRIBUTE_RULES``: a refused option or an
    unknown key raises ``MountSpecError`` naming the option and the target,
    never a silent drop. ``project_dir`` resolves relative bind sources;
    ``volume_name`` is the runtime name the caller resolved for a named
    volume (the entry's ``source`` stands in when it is absent).
    """
    if not isinstance(entry, Mapping):
        raise MountSpecError(f"volume entry must be a mapping, got {type(entry).__name__}")
    sources = [
        str(entry[key]) for key in ("source", "src") if key in entry and entry[key] is not None
    ]
    targets = [
        str(entry[key])
        for key in ("target", "dst", "destination")
        if key in entry and entry[key] is not None
    ]
    if len(sources) > 1:
        raise MountSpecError("use only one of source or src")
    if len(targets) > 1:
        raise MountSpecError("use only one of target, dst, or destination")
    source = sources[0] if sources else ""
    target = targets[0] if targets else ""
    _validate_target(target)

    kind = str(entry.get("type") or ("bind" if is_host_path(source) else "volume"))
    if kind not in ("bind", "volume"):
        if ATTRIBUTE_RULES.get(kind) == REFUSED:
            raise MountSpecError(
                f"unsupported volume type {kind!r} on {target!r}: Apple container "
                "cannot represent it"
            )
        raise MountSpecError(f"unknown volume type {kind!r} on {target!r}")
    _validate_source(source, target)

    readonly = False
    for key, value in entry.items():
        if key in STRUCTURAL_KEYS:
            continue
        if key in LONG_SECTIONS:
            if not isinstance(value, Mapping):
                raise MountSpecError(f"volume option {key!r} on {target!r} must be a mapping")
            for name, sub in value.items():
                attribute_rule(f"{key}.{name}", sub, target)
            continue
        rule = attribute_rule(key, value, target)
        if rule == READ_ONLY:
            if not isinstance(value, bool):
                raise MountSpecError(f"volume option {key!r} on {target!r} must be a boolean")
            readonly = value

    if kind == "bind":
        return Mount("bind", _resolve_host_path(source, project_dir), target, readonly)
    return Mount("volume", volume_name if volume_name is not None else source, target, readonly)


def parse_mount_spec(value: str, *, cwd: str | None = None) -> Mount:
    """Parse Docker's comma-list ``--mount`` dialect into a typed mount."""
    raw: dict[str, str | bool] = {}
    for item in value.split(","):
        key, separator, val = item.partition("=")
        key = key.strip()
        if key not in _LONG_KEYS:
            raise MountSpecError(f"unsupported --mount key {key!r}")
        if key in raw:
            raise MountSpecError(f"duplicate --mount key {key!r}")
        if key in ("ro", "readonly"):
            if separator:
                raise MountSpecError(f"--mount {key} does not take a value")
            raw[key] = True
        else:
            if not separator or not val:
                raise MountSpecError(f"--mount {key} requires a value")
            raw[key] = val

    sources = [str(raw[key]) for key in ("source", "src") if key in raw]
    targets = [str(raw[key]) for key in ("target", "dst", "destination") if key in raw]
    if len(sources) > 1:
        raise MountSpecError("use only one of --mount source or src")
    if len(targets) > 1:
        raise MountSpecError("use only one of --mount target, dst, or destination")
    source = sources[0] if sources else ""
    target = targets[0] if targets else ""
    readonly = bool(raw.get("ro") or raw.get("readonly"))
    mount_type = str(raw.get("type") or ("bind" if is_host_path(source) else "volume"))

    if mount_type == "tmpfs":
        if source:
            raise MountSpecError("tmpfs mounts do not take a source")
        if readonly:
            raise MountSpecError("read-only tmpfs mounts are unsupported")
        _validate_target(target)
        return Mount("tmpfs", "", target)
    if mount_type not in ("bind", "volume"):
        raise MountSpecError(f"unsupported --mount type {mount_type!r}")
    if mount_type == "bind" and not is_host_path(source):
        # Explicit type=bind allows a relative path without a slash.
        source = "./" + source if source else source
    if mount_type == "volume" and is_host_path(source):
        raise MountSpecError("type=volume requires a volume name, not a host path")
    mount = _mount(source, target, readonly, cwd)
    return Mount(mount_type, mount.source, mount.target, mount.readonly)


def parse_mount(value: str, *, cwd: str | None = None) -> list[str]:
    """Parse Docker's comma-list ``--mount`` dialect and emit Apple arguments."""
    return emit(parse_mount_spec(value, cwd=cwd))
