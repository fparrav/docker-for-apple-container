"""Pure Docker volume and mount parsing for Apple ``container run``."""

from __future__ import annotations

import os
from dataclasses import dataclass


class MountSpecError(ValueError):
    """A Docker mount option cannot be represented safely by Apple container."""


@dataclass(frozen=True)
class Mount:
    kind: str
    source: str
    target: str
    readonly: bool = False


_NOOP_SHORT_MODES = frozenset({"rw", "cached", "delegated"})
# Docker's z/Z short modes request SELinux relabeling. Apple container 1.5.0
# documents --mount as type/source/target/readonly only and live-rejects the
# directive ("unknown directive selinux when parsing mount"), so the shim must
# refuse z/Z instead of silently dropping them.
_SELINUX_SHORT_MODES = frozenset({"z", "Z"})
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
        raise MountSpecError("mount targets containing commas are unsupported")


def _mount(source: str, target: str, readonly: bool, cwd: str | None = None) -> Mount:
    _validate_target(target)
    if not source:
        raise MountSpecError("mount source is required")
    if "," in source:
        raise MountSpecError("mount sources containing commas are unsupported")
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


def parse_volume_spec(value: str, *, cwd: str | None = None) -> Mount:
    """Parse Docker ``-v source:target[:mode]`` into a typed mount."""
    parts = value.split(":", 2)
    if len(parts) < 2:
        raise MountSpecError(f"unsupported volume syntax: {value}")
    source, target = parts[:2]
    mode = parts[2] if len(parts) == 3 else ""
    readonly = False
    for flag in (item.strip() for item in mode.split(",")):
        if flag in _SELINUX_SHORT_MODES:
            raise MountSpecError(
                f"volume mode {flag!r} requests SELinux relabeling, which Apple "
                "container cannot represent"
            )
        if flag == "ro":
            readonly = True
        elif flag and flag not in _NOOP_SHORT_MODES:
            raise MountSpecError(f"unsupported volume mode {flag!r} on {target!r}")
    return _mount(source, target, readonly, cwd)


def parse_volume(value: str, *, cwd: str | None = None) -> list[str]:
    """Parse Docker ``-v`` syntax and emit Apple arguments."""
    return emit(parse_volume_spec(value, cwd=cwd))


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
