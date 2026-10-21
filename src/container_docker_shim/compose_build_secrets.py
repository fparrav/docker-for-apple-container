"""Pure Compose build-secret resolution.

Turns one service's ``build.secrets`` entries and the project's top-level
``secrets`` definitions into the Apple ``container build --secret`` arguments,
deciding everything that can be decided without the runtime. A file secret is
delivered by an absolute path resolved against the Compose project directory
(a literal ``$`` in the path is left as is); an environment secret is
delivered by variable name, which the runtime reads from its own process
environment. Nothing here reads a file, reads ``os.environ``, or touches the
runtime: existence and variable checks take an injected ``isfile`` and
``environ``, and no message ever carries a secret value or file contents —
only ids, paths and variable names.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Optional, Sequence

# Compose attributes accepted on a secret but never passed to the build
# (``docker/compose`` ``toBakeSecrets`` reads only ``file``/``environment``);
# each earns a ``NOT APPLIED`` notice instead of a refusal.
IGNORED_ATTRIBUTES = ("uid", "gid", "mode")

# Every attribute a ``build.secrets`` entry may carry.
ENTRY_ATTRIBUTES = ("source", "target", *IGNORED_ATTRIBUTES)

# Every attribute a top-level secret definition may carry.
DEFINITION_ATTRIBUTES = ("name", "file", "environment", "external", *IGNORED_ATTRIBUTES)


@dataclass(frozen=True)
class BuildSecret:
    """One resolved build secret.

    ``kind`` is ``"file"`` or ``"env"``; ``value`` is the absolute path of the
    secret file or the name of the environment variable — never the value.
    """

    id: str
    kind: str
    value: str


def resolve(service: object, entries: object, definitions: object, project_dir: str):
    """Resolve ``build.secrets`` for one service.

    ``service`` is duck-typed (it needs a ``name``). ``entries`` is the
    service's ``build.secrets`` (a list of strings or ``{source, target}``
    mappings, or None); ``definitions`` is the top-level ``secrets`` mapping
    (name to definition, or None); ``project_dir`` is the Compose project
    directory that relative file paths resolve against. Returns
    ``(secrets, problems, notices)``. Every problem and notice names the
    service and the secret id (and the path or variable) and never a value.
    """
    service_name = str(getattr(service, "name", ""))
    definitions = definitions if isinstance(definitions, Mapping) else {}
    secrets: list[BuildSecret] = []
    problems: list[str] = []
    notices: list[str] = []
    for entry in _as_list(entries):
        entry_secrets, entry_problems, entry_notices = _resolve_entry(
            service_name, entry, definitions, project_dir
        )
        secrets.extend(entry_secrets)
        problems.extend(entry_problems)
        notices.extend(entry_notices)
    return secrets, problems, notices


def check(
    secrets: Iterable[BuildSecret],
    isfile: Callable[[str], bool],
    environ: Mapping[str, str],
    service: str = "",
) -> list[str]:
    """Existence problems for resolved secrets, checked without the runtime.

    ``isfile`` reports whether a path exists and ``environ`` is the environment
    mapping; a variable present with an empty value counts as set. ``service``
    names the owning service in the messages. Returns one problem per missing
    file or unset variable; no secret value or file contents appear.
    """
    prefix = f"compose: service {service!r} " if service else "compose: "
    problems: list[str] = []
    for secret in secrets:
        if secret.kind == "file" and not isfile(secret.value):
            problems.append(
                f"{prefix}build secret {secret.id!r} file {secret.value!r} does not exist"
            )
        elif secret.kind == "env" and secret.value not in environ:
            problems.append(
                f"{prefix}build secret {secret.id!r} environment variable "
                f"{secret.value!r} is not set; export it in the environment of this "
                "command (a value only in .env or --env-file does not reach the build)"
            )
    return problems


def to_args(secrets: Iterable[BuildSecret]) -> list[str]:
    """The Apple ``container build --secret`` arguments for the secrets.

    A file secret is ``--secret id=<id>,src=<path>``; an environment secret is
    ``--secret id=<id>,env=<VAR>``. The variable value is never an argument.
    """
    args: list[str] = []
    for secret in secrets:
        if secret.kind == "file":
            args.extend(["--secret", f"id={secret.id},src={secret.value}"])
        elif secret.kind == "env":
            args.extend(["--secret", f"id={secret.id},env={secret.value}"])
    return args


def _resolve_entry(
    service_name: str, entry: object, definitions: Mapping, project_dir: str
) -> tuple[list[BuildSecret], list[str], list[str]]:
    secrets: list[BuildSecret] = []
    problems: list[str] = []
    notices: list[str] = []

    if isinstance(entry, Mapping):
        source = _nonempty_str(entry.get("source"))
        target = _nonempty_str(entry.get("target"))
        entry_attrs: Optional[Mapping] = entry
    else:
        source = _nonempty_str(entry)
        target = None
        entry_attrs = None

    secret_id = target if target is not None else source

    if source is None:
        problems.append(
            f"compose: service {service_name!r} build secret entry has no 'source'"
        )
        return secrets, problems, notices

    definition = definitions.get(source)
    if not isinstance(definition, Mapping):
        problems.append(
            f"compose: service {service_name!r} build secret {source!r} is not defined "
            "in the top-level 'secrets'"
        )
        return secrets, problems, notices

    file_path = _nonempty_str(definition.get("file"))
    environment = _nonempty_str(definition.get("environment"))

    if _is_external(definition.get("external")):
        problems.append(
            f"compose: service {service_name!r} build secret {secret_id!r} is external "
            "and cannot be built"
        )
    elif file_path is not None and environment is not None:
        problems.append(
            f"compose: service {service_name!r} build secret {secret_id!r} declares both "
            "'file' and 'environment'; use only one"
        )
    elif file_path is None and environment is None:
        problems.append(
            f"compose: service {service_name!r} build secret {secret_id!r} needs a "
            "'file' or 'environment' source"
        )

    for attr in _unknown_attrs(definition, DEFINITION_ATTRIBUTES):
        problems.append(
            f"compose: service {service_name!r} build secret {secret_id!r} definition "
            f"has unknown attribute {attr!r}"
        )
    for attr in _ignored_attrs(definition):
        notices.append(_notice(service_name, secret_id, attr))

    if entry_attrs is not None:
        for attr in _unknown_attrs(entry_attrs, ENTRY_ATTRIBUTES):
            problems.append(
                f"compose: service {service_name!r} build secret {secret_id!r} has "
                f"unknown attribute {attr!r}"
            )
        for attr in _ignored_attrs(entry_attrs):
            notices.append(_notice(service_name, secret_id, attr))

    if not problems:
        if file_path is not None:
            secrets.append(
                BuildSecret(
                    id=secret_id, kind="file", value=_resolve_path(file_path, project_dir)
                )
            )
        elif environment is not None:
            secrets.append(BuildSecret(id=secret_id, kind="env", value=environment))

    return secrets, problems, notices


def _notice(service_name: str, secret_id: str, attr: str) -> str:
    return (
        f"NOT APPLIED: service {service_name!r} build secret {secret_id!r} attribute "
        f"{attr!r} is not passed to the build"
    )


def _resolve_path(path: str, project_dir: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(project_dir, path))


def _is_external(value: object) -> bool:
    # ``external: true`` or the legacy object form ``external: {name: x}``.
    return value is True or isinstance(value, Mapping)


def _unknown_attrs(spec: Mapping, known: Sequence[str]) -> list[str]:
    return [str(key) for key in spec if str(key) not in known]


def _ignored_attrs(spec: Mapping) -> list[str]:
    return [attr for attr in IGNORED_ATTRIBUTES if attr in spec]


def _nonempty_str(value: object) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _as_list(value: object) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]
