"""Which services ``docker compose up`` acts on, in what order, and which
networks they use.

Pure logic over ``project.services`` (objects with ``depends_on``,
``depends_required`` and ``networks``): no runtime calls, so it is tested
without Apple ``container``. The result feeds both validation and creation, so
the set that is checked is the set that is created.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .cli import ShimError


@dataclass(frozen=True)
class Plan:
    """Service names to act on, dependencies first, their networks, and notices."""

    ordered: list[str]
    notices: list[str] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)


def _required(service: Any, dep: str) -> bool:
    return bool(getattr(service, "depends_required", {}).get(dep, True))


def _check_undefined(services: dict[str, Any]) -> None:
    """Reject a dependency on a service the project does not define."""
    for name, service in services.items():
        for dep in service.depends_on:
            if dep not in services:
                raise ShimError(
                    f"compose: service {name!r} depends on undefined service {dep!r}", 64
                )


def _check_cycles(services: dict[str, Any]) -> None:
    """Reject a dependency cycle anywhere in the project, naming its members."""
    done: set[str] = set()
    path: list[str] = []

    def visit(name: str) -> None:
        if name in done:
            return
        if name in path:
            members = path[path.index(name):] + [name]
            raise ShimError(
                "compose: cyclic depends_on: " + " -> ".join(repr(m) for m in members), 64
            )
        service = services.get(name)
        if service is None:
            return
        path.append(name)
        for dep in service.depends_on:
            visit(dep)
        path.pop()
        done.add(name)

    for name in services:
        visit(name)


def service_network_keys(spec: dict[str, Any]) -> list[str]:
    """Network keys a service uses, in declared order.

    A mapping or list of keys is read as-is. A service that declares no
    ``networks``, or an empty list or mapping, uses the implicit project
    ``default``, as the official Compose normalizes it.
    """
    declared = spec.get("networks")
    if isinstance(declared, dict) and declared:
        return [str(key) for key in declared]
    if isinstance(declared, list) and declared:
        return [str(item) for item in declared]
    return ["default"]


def plan(project: Any, selected: Iterable[str] | None = None, no_deps: bool = False) -> Plan:
    """Select services and their dependency closure, ordered dependencies first.

    ``selected`` empty or ``None`` means every service. Required dependencies
    are pulled in transitively; an optional one (``required: false``) is kept
    only when its service is already in the plan. ``no_deps`` acts on the
    named services alone. Unknown names exit 1; cycles and dependencies on
    undefined services (optional or not) exit 64.
    """
    services: dict[str, Any] = project.services
    names = list(dict.fromkeys(selected or []))
    unknown = [name for name in names if name not in services]
    if unknown:
        raise ShimError("compose: no such service: " + ", ".join(unknown), 1)
    _check_undefined(services)
    _check_cycles(services)

    chosen = list(names) if names else list(services)
    notices: list[str] = []
    if no_deps:
        skipped = sorted(
            {
                dep
                for name in chosen
                for dep in services[name].depends_on
                if dep not in chosen
            }
        )
        if skipped:
            notices.append(
                "dependencies not started (--no-deps): " + ", ".join(skipped)
            )
    else:
        in_plan = set(chosen)
        pending = list(chosen)
        while pending:
            name = pending.pop()
            for dep in services[name].depends_on:
                if dep in in_plan or not _required(services[name], dep):
                    continue
                in_plan.add(dep)
                pending.append(dep)
        chosen = [name for name in services if name in in_plan]

    members = set(chosen)
    ordered: list[str] = []
    seen: set[str] = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        for dep in services[name].depends_on:
            if dep in members:
                visit(dep)
        ordered.append(name)

    for name in chosen:
        visit(name)
    networks: list[str] = []
    for name in ordered:
        for key in service_network_keys(services[name].spec):
            if key not in networks:
                networks.append(key)
    return Plan(ordered, notices, networks)
