"""Pure Compose resource identity resolution.

Resolves every network and volume a compose project declares to exactly one
runtime name and flags ownership, mirroring Docker Compose: an explicit
``name:`` wins, ``external`` resources keep their own name (or bare key), and
managed resources take ``<project>_<key>``. A service volume entry with no
source (an anonymous volume) takes ``<project>_<service>_anon-<hash>`` so a
recreation reuses the same volume. Nothing in this module calls the Apple
``container`` runtime, so the rules are unit-testable without it.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional

LABEL_PROJECT = "com.docker.compose.project"
LABEL_NETWORK = "com.docker.compose.network"
LABEL_VOLUME = "com.docker.compose.volume"
LABEL_SERVICE = "com.docker.compose.service"
LABEL_ANONYMOUS = "com.docker.compose.anonymous"


@dataclass(frozen=True)
class Resource:
    """One compose network or volume with its resolved runtime identity.

    ``managed`` is the inverse of ``external`` and is derived, never taken
    from the file. ``labels`` holds the labels the user declared for the
    resource (``labels:`` dict or ``key=value`` list in the compose file),
    normalized to a mapping; it is not where ownership is decided.
    """

    key: str
    name: str
    external: bool
    managed: bool = field(init=False)
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "managed", not self.external)


@dataclass(frozen=True)
class Resources:
    """All resolved networks, volumes and anonymous volumes of one project.

    The mappings are keyed by the compose file's key; the value's ``name``
    is the single runtime name every use of the resource must carry.
    ``anonymous`` holds one entry per service volume target with no source,
    in service order then entry order.
    """

    project: str
    networks: dict[str, Resource]
    volumes: dict[str, Resource]
    anonymous: tuple[Resource, ...] = ()


def resolve(project: object) -> Resources:
    """Resolve the resources declared by ``project``.

    ``project`` is duck-typed: it needs a ``name`` (str) plus ``networks``
    and ``volumes`` (each a mapping of key to spec, or None); each spec may
    be None or a mapping. The runtime name of a resource comes, in order,
    from the model's ``name`` (the official normalizer fills it), else the
    key when the resource is external, else ``<project>_<key>``. An
    undeclared ``default`` network is added as managed. A ``services``
    mapping (name to spec, or to an object with a ``spec`` attribute)
    contributes the anonymous volumes.
    """
    project_name = str(project.name)
    networks = _resolve_family(getattr(project, "networks", None), project_name)
    volumes = _resolve_family(getattr(project, "volumes", None), project_name)
    if "default" not in networks:
        networks["default"] = _resource("default", None, project_name)
    anonymous = _resolve_anonymous(getattr(project, "services", None), project_name)
    return Resources(project_name, networks, volumes, anonymous)


def anonymous_volume(project: str, service: str, target: str) -> Resource:
    """The deterministic identity of one service's anonymous volume at ``target``.

    The name hashes the target so the same declaration resolves to the same
    volume on every run, and the labels let ``down -v`` find it by project.
    """
    digest = hashlib.sha256(target.encode()).hexdigest()[:10]
    return Resource(
        key=target,
        name=f"{project}_{service}_anon-{digest}",
        external=False,
        labels={LABEL_PROJECT: project, LABEL_SERVICE: service, LABEL_ANONYMOUS: target},
    )


def removable(
    resources: Iterable[Resource],
    labelled: Mapping[str, str],
    project: str,
) -> list[Resource]:
    """Resources this project owns, in the order received.

    A resource is removable only when the model declares it managed and the
    observed ``com.docker.compose.project`` label on its runtime name
    (``labelled`` maps runtime name to observed value) equals ``project``.
    External resources, resources labelled for another project, and
    resources without (or with an empty) label are excluded.
    """
    kept: list[Resource] = []
    for resource in resources:
        if not resource.managed:
            continue
        value = labelled.get(resource.name)
        if isinstance(value, str) and value and value == project:
            kept.append(resource)
    return kept


def _resolve_family(spec: Optional[Mapping], project_name: str) -> dict[str, Resource]:
    out: dict[str, Resource] = {}
    for key, value in (spec or {}).items():
        out[str(key)] = _resource(str(key), value, project_name)
    return out


def _resource(key: str, value: object, project_name: str) -> Resource:
    spec = value if isinstance(value, Mapping) else {}
    external = _is_external(spec.get("external"))
    name = _nonempty_str(spec.get("name"))
    if name is None and external:
        name = _legacy_external_name(spec.get("external"))
    if name is None and external:
        name = key
    if name is None:
        name = f"{project_name}_{key}"
    return Resource(key=key, name=name, external=external, labels=_labels(spec.get("labels")))


def _is_external(value: object) -> bool:
    # ``external: true`` or the legacy object form ``external: {name: x}``.
    return value is True or isinstance(value, Mapping)


def _legacy_external_name(value: object) -> Optional[str]:
    if isinstance(value, Mapping):
        return _nonempty_str(value.get("name"))
    return None


def _nonempty_str(value: object) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _labels(value: object) -> dict[str, str]:
    if isinstance(value, Mapping):
        return {str(key): str(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        out: dict[str, str] = {}
        for entry in value:
            key, sep, val = str(entry).partition("=")
            out[key] = val if sep else ""
        return out
    return {}


def _resolve_anonymous(services: object, project_name: str) -> tuple[Resource, ...]:
    """The project's anonymous volumes, in service order then entry order.

    ``services`` is a mapping of service name to spec (or to an object whose
    ``spec`` attribute is the mapping), or None; identical (service, target)
    pairs collapse to one resource.
    """
    if not isinstance(services, Mapping):
        return ()
    out: list[Resource] = []
    seen: set[tuple[str, str]] = set()
    for service_name, entry in services.items():
        name = str(service_name)
        for target in _anonymous_targets(entry):
            pair = (name, target)
            if pair in seen:
                continue
            seen.add(pair)
            out.append(anonymous_volume(project_name, name, target))
    return tuple(out)


def _service_spec(entry: object) -> Mapping:
    """The spec mapping of a service entry: the mapping itself, or its ``spec``."""
    if isinstance(entry, Mapping):
        return entry
    spec = getattr(entry, "spec", None)
    return spec if isinstance(spec, Mapping) else {}


def _anonymous_targets(entry: object) -> list[str]:
    """The anonymous volume targets of one service, in entry order."""
    volumes = _service_spec(entry).get("volumes")
    if not isinstance(volumes, (list, tuple)):
        return []
    targets: list[str] = []
    for item in volumes:
        target = _anonymous_target(item)
        if target is not None:
            targets.append(target)
    return targets


def _anonymous_target(entry: object) -> Optional[str]:
    """The target of one anonymous volume entry, or None when it is not one.

    Short syntax: an absolute path with no ``:`` (``/app/node_modules``).
    Long syntax: a mapping whose ``type`` is ``volume`` (or absent) with a
    target and no source; bind, tmpfs, image and named volumes are not.
    """
    if isinstance(entry, str):
        if entry.startswith("/") and ":" not in entry:
            return entry
        return None
    if not isinstance(entry, Mapping):
        return None
    volume_type = entry.get("type")
    if volume_type is not None and volume_type != "volume":
        return None
    if _nonempty_str(entry.get("source")) or _nonempty_str(entry.get("src")):
        return None
    return _nonempty_str(entry.get("target"))
