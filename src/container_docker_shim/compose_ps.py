"""Pure Docker Compose ``ps`` output renderers."""

from __future__ import annotations

import shlex
from typing import Any

from .cli import _human_age

JSON_FIELDS = (
    "Command", "CreatedAt", "Engine", "ExitCode", "Health", "ID", "Image",
    "Labels", "LocalVolumes", "Mounts", "Name", "Names", "Networks", "Ports",
    "Project", "Publishers", "RunningFor", "Service", "Size", "State", "Status",
)
TABLE_FIELDS = ("NAME", "IMAGE", "COMMAND", "SERVICE", "CREATED", "STATUS", "PORTS")
TABLE_KEYS = ("Name", "Image", "Command", "Service", "RunningFor", "Status", "Ports")


def _command(row: dict[str, Any]) -> str:
    words = [str(row.get("path") or "")]
    words.extend(str(arg) for arg in row.get("args") or [])
    command = shlex.join([word for word in words if word])
    return f'"{command}"' if command else ""


def _exit_code(row: dict[str, Any]) -> int | None:
    """Return the row's numeric exit code, or ``None`` when it is absent.

    Apple container 1.5.0's list/inspect JSON exposes no exit-code field, so
    an exited row from the real runtime always yields ``None``; only a caller
    that verified a code (e.g. a scripted fake runtime) supplies one.
    """
    raw = row.get("exit_code")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def exited_rows_without_exit_code(rows: list[dict[str, Any]]) -> list[str]:
    """Names of exited rows whose numeric exit code is unknown."""
    return [
        str(row.get("name") or row.get("id") or "?")
        for row in rows
        if str(row.get("state") or "") == "exited" and _exit_code(row) is None
    ]


def _record(row: dict[str, Any]) -> dict[str, Any]:
    labels = row.get("labels") or {}
    service = str(labels.get("com.docker.compose.service") or "")
    project = str(labels.get("com.docker.compose.project") or "")
    state = str(row.get("state") or "")
    exit_code = _exit_code(row)
    running_for = str(row.get("running_for") or _human_age(row.get("started_at") or row.get("created_at")) or "Less than a second ago")
    if running_for in {"0 seconds ago", "1 second ago"}:
        running_for = "Less than a second ago"
    if state == "running":
        status = "Up " + running_for.removesuffix(" ago")
    elif state == "exited":
        # The code Docker prints in parentheses is omitted, not zeroed, when
        # the runtime exposes none.
        detail = f"({exit_code}) " if exit_code is not None else ""
        status = f"Exited {detail}{running_for}"
    elif state == "created":
        status = "Created"
    else:
        status = state.capitalize()
    mounts = row.get("mounts") or []
    networks = row.get("networks") or []
    ports = row.get("ports") or []
    return {
        "Command": _command(row),
        "CreatedAt": str(row.get("created_at") or ""),
        "Engine": "",
        "ExitCode": 0 if exit_code is None else exit_code,
        "Health": "",
        "ID": str(row.get("id") or "")[:12],
        "Image": str(row.get("image") or ""),
        "Labels": ",".join(f"{key}={value}" for key, value in sorted(labels.items())),
        "LocalVolumes": str(sum(1 for mount in mounts if mount.get("type") == "volume")),
        "Mounts": ",".join(str(mount.get("source") or "") for mount in mounts),
        "Name": str(row.get("name") or ""),
        "Names": str(row.get("name") or ""),
        "Networks": ",".join(str(network) for network in networks),
        "Ports": ",".join(str(port) for port in ports),
        "Project": project,
        "Publishers": [],
        "RunningFor": running_for,
        "Service": service,
        "Size": "0B",
        "State": state,
        "Status": status,
    }


def render_json(rows: list[dict[str, Any]]) -> str:
    """Return Compose's JSON-lines layout with stable reference field types.

    Refuses (raises) instead of fabricating a value: Compose prints an integer
    ``ExitCode``, and an exited row without a verified numeric code cannot be
    rendered honestly.
    """
    import json

    missing = exited_rows_without_exit_code(rows)
    if missing:
        raise ValueError(
            "exited container without a numeric exit code: " + ", ".join(missing)
        )
    return "".join(json.dumps(_record(row), separators=(",", ":")) + "\n" for row in rows)


def render_table(rows: list[dict[str, Any]]) -> str:
    """Render Compose's default table, including the header for an empty set."""
    if not rows:
        return "NAME      IMAGE     COMMAND   SERVICE   CREATED   STATUS    PORTS\n"
    records = [_record(row) for row in rows]
    values = [[record[key] for key in TABLE_KEYS] for record in records]
    widths = [max([len(header), *(len(value[i]) for value in values)]) + 1 for i, header in enumerate(TABLE_FIELDS)]
    lines = [
        "  ".join(
            [*(header.ljust(widths[i]) for i, header in enumerate(TABLE_FIELDS[:-1])), TABLE_FIELDS[-1]]
        )
    ]
    lines.extend(
        "  ".join([*(value[i].ljust(widths[i]) for i in range(len(TABLE_FIELDS) - 1)), value[-1]])
        for value in values
    )
    return "\n".join(lines) + "\n"
