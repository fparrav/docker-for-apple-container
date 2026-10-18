"""Compose config reconciliation: the hash that decides recreation.

:func:`config_hash` digests the normalized service ``spec`` together
with the resolved image ID. ``compose up`` recreates a container when
the currently computed hash differs from the one stored on the
running container. ``depends_on``, ``build`` and ``x-`` extension
keys are excluded from the hash: they are planning metadata, not
part of the container's runtime configuration.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

LABEL_CONFIG_HASH = "com.docker.compose.config-hash"
LABEL_IMAGE = "com.docker.compose.image"


def config_hash(spec: dict[str, Any], image_id: str) -> str:
    """SHA-256 hex digest of the service ``spec`` plus ``image_id``.

    ``depends_on``, ``build`` and every ``x-`` extension key are
    dropped from a copy of ``spec`` (the argument is never mutated);
    the remaining mapping is canonicalized with
    ``json.dumps(..., sort_keys=True, separators=(",", ":"))`` -- so
    key order never matters -- and hashed together with ``image_id``
    as the mapping ``{"spec": ..., "image": ...}`` (``default=str``
    covers any exotic value). Identical configuration in a different
    key order hashes identically; any change to a remaining value or
    to ``image_id`` changes the hash.
    """
    cleaned = {
        key: value
        for key, value in spec.items()
        if key not in ("depends_on", "build") and not key.startswith("x-")
    }
    canonical = json.dumps(
        {"spec": cleaned, "image": image_id},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def decide(
    existing: dict[str, Any] | None,
    wanted_hash: str,
    wanted_image: str,
    force: bool = False,
    no_recreate: bool = False,
) -> str:
    """Decide what ``compose up`` does with a service's container.

    ``existing`` is ``None`` when no container exists yet, or a dict
    describing the container with a ``labels`` mapping (str to str,
    may be missing or ``None``) and a ``state`` string (``"running"``,
    or anything else meaning stopped). Returns one of:

    - ``"create"`` -- no container exists yet; always chosen, even
      under ``no_recreate``.
    - ``"recreate"`` -- ``force`` is set, or the stored
      :data:`LABEL_CONFIG_HASH` label is missing or differs from
      ``wanted_hash``, or :data:`LABEL_IMAGE` differs from
      ``wanted_image``.
    - ``"start"`` -- the configuration already matches but the
      container is stopped.
    - ``"keep"`` -- the configuration matches and the container is
      already running.

    ``no_recreate`` downgrades a ``"recreate"`` to ``"keep"`` for a
    running container and to ``"start"`` for a stopped one; combining
    it with ``force`` raises :class:`ValueError`.
    """
    if force and no_recreate:
        raise ValueError("--force-recreate and --no-recreate are incompatible")
    if existing is None:
        return "create"
    labels = existing.get("labels") or {}
    state = existing.get("state")
    if (
        force
        or LABEL_CONFIG_HASH not in labels
        or labels[LABEL_CONFIG_HASH] != wanted_hash
        or labels.get(LABEL_IMAGE) != wanted_image
    ):
        action = "recreate"
    elif state == "running":
        action = "keep"
    else:
        action = "start"
    if no_recreate and action == "recreate":
        return "keep" if state == "running" else "start"
    return action
