"""`docker compose` subset over Apple `container` — stateless orchestration.

State model
-----------
This module persists **nothing** on the macOS host. There is no sidecar
file, project registry, SQLite database, or cache owned by the shim. All
durable state lives in Apple `container` (the containers, the project
network, and named volumes), annotated with Docker-compatible labels:

    com.docker.compose.project           the project a resource belongs to
    com.docker.compose.service           the service a container implements
    com.docker.compose.container-number  always 1 (no replicas)
    com.docker.compose.oneoff            always "False"

Every runtime verb reconstructs the project by querying Apple and filtering on
these labels — exactly the schema Docker Compose itself uses. `up`, `pull`,
`build`, and `config` require the compose file. `start`, `stop`, `restart`, and
`rm` use it for dependency order when available, with stable service-name order
from labels as a fallback. `down`, `ps`, `logs`, `exec`, and `ls` need only the
runtime labels.

Service discovery
-----------------
Apple `container` does not resolve service names by DNS without an admin
`container system dns` domain, and has no `--add-host`. The shim closes the
gap in two layers, both writing only to **each container's own ephemeral
/etc/hosts** (gone when the container is removed — the macOS host's
/etc/hosts and resolver are never touched):

1. **Boot-time, for dependencies.** Services start in `depends_on` order, so
   by the time a dependent service launches its dependencies' addresses are
   already known. The dependent's entrypoint is wrapped in a `/bin/sh`
   prelude that writes ``<ip> <dependency>`` lines before exec'ing the real
   process — so an app that dials its database in its first millisecond
   still resolves the name. (Post-start injection alone loses that race:
   the app crashes before `container exec` can land, and Apple `container`
   has no restart policies to save it.) Opt out per service with
   ``x-shim-boot-hosts: false``.

2. **Post-start, for all peers.** After everything is up, ``<ip> <service>``
   lines for the whole project are appended idempotently into every
   container via `container exec` — covering peers that are not declared
   dependencies.

Both layers also publish ``host.docker.internal`` and
``gateway.docker.internal`` → the container's gateway (which, on Apple
`container`, is the macOS host). Docker Desktop does this automatically on
macOS/Windows; reproducing it here lets images that dial the host by that
name work unchanged.
"""

from __future__ import annotations

import dataclasses
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Optional

from . import compose_build_secrets
from . import compose_capabilities
from .cli import (
    DOCKER_ZERO_TIME,
    ShimError,
    _container_bin,
    _die,
    _load_container_rows,
    _normalize_cpus,
    _normalize_memory,
    _run_container_capture,
    _run_container_passthrough,
    cmd_exec as docker_exec,
    cmd_image as docker_image,
    cmd_rm as docker_rm,
    cmd_start as docker_start,
    cmd_stop as docker_stop,
    translate_build_argv,
)
from .mounts import Mount, MountSpecError, is_host_path, mount_from_entry, parse_volume_spec
from .mounts import emit as emit_mount
from . import security
from . import compose_health
from . import compose_normalizer
from . import compose_plan
from . import compose_reconcile
from . import compose_resource_compat
from . import compose_resources
from . import compose_subset
from .compose_ps import (
    exited_rows_without_exit_code as _exited_without_exit_code,
    render_json as _render_ps_json,
    render_table as _render_ps_table,
)


# --------------------------------------------------------------------------- #
# Label schema (Docker Compose's own keys — Apple stores them verbatim).
# --------------------------------------------------------------------------- #

LABEL_PROJECT = "com.docker.compose.project"
LABEL_SERVICE = "com.docker.compose.service"
LABEL_NUMBER = "com.docker.compose.container-number"
LABEL_ONEOFF = "com.docker.compose.oneoff"
LABEL_CONFIG_HASH = compose_reconcile.LABEL_CONFIG_HASH
LABEL_IMAGE = compose_reconcile.LABEL_IMAGE

SUPPORTED_COMPOSE_FILENAMES = (
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
)

# Per-service escape hatch: `x-shim-boot-hosts: false` skips the boot-time
# /etc/hosts entrypoint wrapper for that service (post-start injection still
# applies). For `depends_on` services whose image has no /bin/sh.
BOOT_HOSTS_KEY = "x-shim-boot-hosts"

# Derived views of the capability table (`compose_capabilities.SERVICE_KEYS`),
# which is the only place that decides what a service key means: the keys the
# shim applies, and the keys classified but not applied (refused, or reported
# with a NOT APPLIED line).
SECURITY_OPTION_KEYS = dict(compose_capabilities.SECURITY_KEYS)

UNSUPPORTED_SERVICE_KEYS = {
    key: rule.reason
    for key, rule in compose_capabilities.SERVICE_KEYS.items()
    if rule.status != compose_capabilities.SUPPORTED and key not in SECURITY_OPTION_KEYS
}

SUPPORTED_SERVICE_KEYS = frozenset(
    {
        key
        for key, rule in compose_capabilities.SERVICE_KEYS.items()
        if rule.status == compose_capabilities.SUPPORTED
        and rule.reason != compose_capabilities.NO_EFFECT
    }
    | {BOOT_HOSTS_KEY}
)


# ========================================================================== #
# Minimal YAML parser (compose subset)
#
# A hand-rolled, dependency-free parser covering the YAML that compose files
# actually use: indentation-based block maps and sequences, inline flow
# collections (``[a, b]`` / ``{k: v}``), quoted and bare scalars, and ``#``
# comments. It is deliberately not a full YAML implementation — anchors,
# multi-document streams, and ``|``/``>`` block scalars are out of scope.
# ========================================================================== #


def _strip_comment(line: str) -> str:
    """Drop a trailing ``#`` comment, respecting quotes."""
    quote: str | None = None
    for idx, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (idx == 0 or line[idx - 1] in (" ", "\t")):
            return line[:idx]
    return line


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Return ``(indent, content)`` for every non-blank, non-comment line."""
    out: list[tuple[int, str]] = []
    for raw in text.splitlines():
        stripped = _strip_comment(raw.replace("\t", "    ")).rstrip()
        if not stripped.strip():
            continue
        if stripped.strip() == "---":  # tolerate a single document marker
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        out.append((indent, stripped.strip()))
    return out


def _split_key(content: str) -> tuple[str, str] | None:
    """Split ``key: value`` at the first unquoted colon. None if not a map line."""
    quote: str | None = None
    for idx, ch in enumerate(content):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == ":" and (idx + 1 == len(content) or content[idx + 1] == " "):
            return content[:idx].strip(), content[idx + 1 :].strip()
    return None


def _scalar(token: str) -> Any:
    """Type a bare scalar token the way compose expects."""
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        return token[1:-1]
    low = token.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~", ""):
        return None
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token


def _split_flow(body: str) -> list[str]:
    """Split a flow collection body on top-level commas, respecting nesting."""
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    buf: list[str] = []
    for ch in body:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch in ("[", "{"):
            depth += 1
            buf.append(ch)
        elif ch in ("]", "}"):
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_value(token: str) -> Any:
    """Parse an inline value: flow sequence, flow mapping, or scalar."""
    token = token.strip()
    if token.startswith("[") and token.endswith("]"):
        return [_parse_value(part) for part in _split_flow(token[1:-1])]
    if token.startswith("{") and token.endswith("}"):
        result: dict[str, Any] = {}
        for part in _split_flow(token[1:-1]):
            kv = _split_key(part)
            if kv is None:
                continue
            result[str(_scalar(kv[0]))] = _parse_value(kv[1]) if kv[1] else None
        return result
    return _scalar(token)


def _parse_block(lines: list[tuple[int, str]], i: int, indent: int) -> tuple[Any, int]:
    first = lines[i][1]
    if first == "-" or first.startswith("- ") or first.startswith("-\t"):
        return _parse_seq(lines, i, indent)
    return _parse_map(lines, i, indent)


def _parse_map(lines: list[tuple[int, str]], i: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while i < len(lines):
        ind, content = lines[i]
        if ind < indent:
            break
        if ind > indent:
            raise ShimError(f"compose: unexpected indentation near {content!r}", 64)
        kv = _split_key(content)
        if kv is None:
            raise ShimError(f"compose: expected 'key: value' near {content!r}", 64)
        key, rest = kv
        key = str(_scalar(key))
        i += 1
        if rest:
            result[key] = _parse_value(rest)
            continue
        # Nested block: a deeper map/seq, or a sequence at the same indent.
        if i < len(lines):
            nind, ncontent = lines[i]
            is_seq = ncontent == "-" or ncontent.startswith("- ")
            if nind > indent:
                result[key], i = _parse_block(lines, i, nind)
                continue
            if nind == indent and is_seq:
                result[key], i = _parse_seq(lines, i, indent)
                continue
        result[key] = None
    return result, i


def _parse_seq(lines: list[tuple[int, str]], i: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while i < len(lines):
        ind, content = lines[i]
        if ind != indent or not (content == "-" or content.startswith("- ")):
            break
        item = content[1:].strip()
        i += 1
        if not item:
            if i < len(lines) and lines[i][0] > indent:
                value, i = _parse_block(lines, i, lines[i][0])
            else:
                value = None
            result.append(value)
            continue
        if _split_key(item) is not None and not item.startswith(("[", "{")):
            # A mapping that begins inline after the dash: gather its inline
            # entry plus any deeper continuation lines into one synthetic map.
            sub: list[tuple[int, str]] = [(indent + 2, item)]
            while i < len(lines) and lines[i][0] > indent:
                sub.append(lines[i])
                i += 1
            value, _ = _parse_map(sub, 0, indent + 2)
            result.append(value)
            continue
        result.append(_parse_value(item))
    return result, i


def parse_yaml(text: str) -> Any:
    """Parse a compose-subset YAML document into Python data."""
    lines = _logical_lines(text)
    if not lines:
        return {}
    value, end = _parse_block(lines, 0, lines[0][0])
    if end != len(lines):
        raise ShimError(f"compose: could not parse near {lines[end][1]!r}", 64)
    return value


# ========================================================================== #
# Environment interpolation (${VAR}, ${VAR:-default}, ${VAR:?msg}, $$)
# ========================================================================== #


def _load_env_file(path: str, *, required: bool = False) -> dict[str, str]:
    """Read a dotenv file; a missing file is silent unless ``required`` (explicit ``--env-file``)."""
    env: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :].strip()
                key, sep, value = line.partition("=")
                if not sep:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                env[key.strip()] = value
    except FileNotFoundError:
        if required:
            raise ShimError(f"couldn't find env file: {os.path.abspath(path)}", 1)
    return env


def _interpolate_str(value: str, env: dict[str, str]) -> str:
    out: list[str] = []
    i = 0
    n = len(value)
    while i < n:
        ch = value[i]
        if ch != "$":
            out.append(ch)
            i += 1
            continue
        if i + 1 < n and value[i + 1] == "$":  # $$ -> literal $
            out.append("$")
            i += 2
            continue
        if i + 1 < n and value[i + 1] == "{":
            close = value.find("}", i + 2)
            if close == -1:
                out.append(ch)
                i += 1
                continue
            expr = value[i + 2 : close]
            out.append(_resolve_expr(expr, env))
            i = close + 1
            continue
        # Bare $VAR form.
        j = i + 1
        while j < n and (value[j].isalnum() or value[j] == "_"):
            j += 1
        name = value[i + 1 : j]
        if name:
            out.append(env.get(name, ""))
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _resolve_expr(expr: str, env: dict[str, str]) -> str:
    for sep, required in ((":?", True), (":-", False), ("?", True), ("-", False)):
        if sep in expr:
            name, _, tail = expr.partition(sep)
            present = env.get(name)
            if present not in (None, ""):
                return present
            if required:
                raise ShimError(f"compose: required variable {name!r} is unset: {tail}", 1)
            return tail
    return env.get(expr, "")


def _interpolate(obj: Any, env: dict[str, str]) -> Any:
    if isinstance(obj, str):
        return _interpolate_str(obj, env)
    if isinstance(obj, list):
        return [_interpolate(item, env) for item in obj]
    if isinstance(obj, dict):
        return {key: _interpolate(val, env) for key, val in obj.items()}
    return obj


# ========================================================================== #
# Project model
# ========================================================================== #


def _sanitize_project_name(raw: str) -> str:
    """Reduce a name to Docker's project charset: [a-z0-9_-]."""
    lowered = raw.lower()
    cleaned = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in lowered)
    cleaned = cleaned.lstrip("_-")
    return cleaned or "default"


def _as_str(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    return str(value)


DEPENDS_CONDITIONS = ("service_started", "service_healthy", "service_completed_successfully")


class Service:
    """A normalized compose service."""

    def __init__(self, name: str, spec: dict[str, Any]):
        self.name = name
        self.spec = spec
        self.depends_on = _parse_depends_on(spec.get("depends_on"))
        self.depends_conditions: dict[str, str] = _parse_depends_conditions(name, spec.get("depends_on"))
        self.depends_required: dict[str, bool] = _parse_depends_required(name, spec.get("depends_on"))

    @property
    def container_name(self) -> str | None:
        value = self.spec.get("container_name")
        return _as_str(value) if value else None


def _parse_depends_on(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, dict):  # map form: {db: {condition: ...}}
        return [str(key) for key in value]
    return []


def _parse_depends_conditions(service_name: str, value: Any) -> dict[str, str]:
    """Map each dependency to its condition, defaulting to `service_started`."""
    if value is None:
        return {}
    if isinstance(value, str):
        value = [value]
    if isinstance(value, dict):  # map form: {db: {condition: ...}}
        entries = list(value.items())
    elif isinstance(value, list):
        entries = [(item, None) for item in value]
    else:
        return {}
    allowed = ", ".join(DEPENDS_CONDITIONS[:-1]) + " or " + DEPENDS_CONDITIONS[-1]
    conditions: dict[str, str] = {}
    for key, entry in entries:
        name = str(key)
        condition = entry.get("condition") if isinstance(entry, dict) else entry
        if condition in (None, ""):
            condition = "service_started"
        else:
            condition = str(condition)
        if condition not in DEPENDS_CONDITIONS:
            raise ShimError(
                f"compose: service {service_name!r}: depends_on {name!r} "
                f"has unsupported condition {condition!r} (use {allowed})",
                64,
            )
        conditions[name] = condition
    return conditions


def _parse_depends_required(service_name: str, value: Any) -> dict[str, bool]:
    """Map each dependency to ``required`` (default True; only the map form sets it)."""
    if isinstance(value, str):
        value = [value]
    if isinstance(value, list):
        return {str(item): True for item in value}
    if not isinstance(value, dict):
        return {}
    required: dict[str, bool] = {}
    for key, entry in value.items():
        flag = entry.get("required") if isinstance(entry, dict) else None
        if flag is None or flag is True or flag == "true":
            required[str(key)] = True
        elif flag is False or flag == "false":
            required[str(key)] = False
        else:
            raise ShimError(
                f"compose: service {service_name!r}: depends_on {str(key)!r} "
                f"has an invalid 'required' value {flag!r} (use true or false)",
                64,
            )
    return required


class Project:
    """A parsed, interpolated compose project."""

    def __init__(self, name: str, doc: dict[str, Any], directory: str):
        self.name = name
        self.directory = directory
        self.doc = doc
        raw_services = doc.get("services") or {}
        if not isinstance(raw_services, dict):
            raise ShimError("compose: 'services' must be a mapping", 64)
        self.services: dict[str, Service] = {}
        for svc_name, spec in raw_services.items():
            if spec is None:
                spec = {}
            if not isinstance(spec, dict):
                raise ShimError(f"compose: service {svc_name!r} must be a mapping", 64)
            self.services[str(svc_name)] = Service(str(svc_name), spec)
        self.networks = doc.get("networks") or {}
        self.volumes = doc.get("volumes") or {}
        self.secrets = doc.get("secrets") or {}
        # The official normalizer's JSON as received; None for the built-in parser.
        self.normalized: dict[str, Any] | None = None
        self._resources: compose_resources.Resources | None = None

    @property
    def resources(self) -> compose_resources.Resources:
        """Networks and volumes with their single resolved runtime name each."""
        if self._resources is None:
            self._resources = compose_resources.resolve(self)
        return self._resources

    def topo_sorted(self) -> list[Service]:
        """Services in dependency order (dependencies first), cycle-detected."""
        ordered: list[Service] = []
        done: set[str] = set()
        visiting: set[str] = set()

        def visit(name: str) -> None:
            if name in done:
                return
            if name in visiting:
                raise ShimError(f"compose: cyclic depends_on involving {name!r}", 64)
            service = self.services.get(name)
            if service is None:
                return  # depends_on a service not defined here — ignore, like Docker
            visiting.add(name)
            for dep in service.depends_on:
                visit(dep)
            visiting.discard(name)
            done.add(name)
            ordered.append(service)

        for svc_name in self.services:
            visit(svc_name)
        return ordered


# ========================================================================== #
# Loading & project-name resolution
# ========================================================================== #


def _find_compose_file(explicit: str | None, directory: str) -> str:
    if explicit:
        # Like Compose, an explicit -f path is relative to the working directory;
        # only the default file is searched for in the project directory.
        path = os.path.abspath(explicit)
        if not os.path.isfile(path):
            raise ShimError(f"compose: file not found: {explicit}", 1)
        return path
    for name in SUPPORTED_COMPOSE_FILENAMES:
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate):
            return candidate
    raise ShimError(
        "compose: no compose file found "
        "(looked for compose.yaml, compose.yml, docker-compose.yaml, docker-compose.yml)",
        1,
    )


def _find_compose_files(files: list[str], directory: str) -> list[str]:
    """Resolve every explicit ``-f`` file, or discover the default one."""
    if files:
        return [_find_compose_file(item, directory) for item in files]
    return [_find_compose_file(None, directory)]


def _launcher_paths() -> list[str]:
    """Paths that identify this shim, so a normalizer cannot resolve back to it."""
    source_launcher = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "bin",
        "docker",
    )
    return [path for path in (sys.argv[0], source_launcher) if path]


def _resolve_normalizer() -> str | None:
    """The official `docker-compose` to normalize with, or None for the built-in parser."""
    try:
        return compose_normalizer.resolve_executable(
            os.environ, shutil.which, _launcher_paths()
        )
    except compose_normalizer.NormalizerError as exc:
        raise ShimError(f"compose: {exc}", exc.code)


NORMALIZER_TIMEOUT_SECONDS = 120


def _run_normalizer(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=NORMALIZER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        # load_normalized reports an OSError as a NormalizerError.
        raise OSError(
            f"{os.path.basename(argv[0])} did not finish within "
            f"{NORMALIZER_TIMEOUT_SECONDS} seconds"
        )


def _normalizer_inputs(
    files: list[str],
    profiles: list[str],
    compose_paths: list[str],
    project_name: str | None,
    project_dir: str | None,
    env_files: tuple[str, ...],
) -> compose_normalizer.Inputs:
    """Flags for the official binary; defaults are left to it so overrides still merge."""
    return compose_normalizer.Inputs(
        files=tuple(compose_paths) if files else (),
        env_files=tuple(env_files),
        profiles=tuple(profiles),
        project_name=project_name,
        project_dir=project_dir,
    )


def _load_normalized_model(
    executable: str, inputs: compose_normalizer.Inputs
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run `docker-compose config`; return the orchestrator model and the raw JSON."""
    try:
        raw, warnings = compose_normalizer.load_normalized(
            executable, inputs, _run_normalizer, os.environ
        )
        if warnings.strip():
            sys.stderr.write(warnings if warnings.endswith("\n") else warnings + "\n")
        version = compose_normalizer.executable_version(
            executable, _run_normalizer, os.environ
        )
        return compose_normalizer.convert_model(raw, version), raw
    except compose_normalizer.NormalizerError as exc:
        raise ShimError(f"compose: {exc}", exc.code)


_UNDEFINED_DEPENDENCY = re.compile(
    r'service "([^"]+)" depends on undefined service "([^"]+)"'
)


def _inactive_profile_hint(
    executable: str, inputs: compose_normalizer.Inputs, message: str
) -> str | None:
    """Say which profile hides a dependency the normalizer reports as undefined.

    The official normalizer drops services of inactive profiles, so a
    dependency on one surfaces as "undefined service". Re-reading the project
    with every profile enabled tells whether the service exists and which
    profile it needs. Any failure here keeps the original message.
    """
    found = _UNDEFINED_DEPENDENCY.search(message)
    if found is None:
        return None
    service, dependency = found.groups()
    try:
        raw, _warnings = compose_normalizer.load_normalized(
            executable,
            dataclasses.replace(inputs, profiles=("*",)),
            _run_normalizer,
            os.environ,
        )
    except compose_normalizer.NormalizerError:
        return None
    declared = (raw.get("services") or {}).get(dependency) if isinstance(raw, dict) else None
    needed = declared.get("profiles") if isinstance(declared, dict) else None
    if not isinstance(needed, list) or not needed:
        return None
    names = ", ".join(str(item) for item in needed)
    return (
        f'compose: service "{service}" depends on service "{dependency}", which is only '
        f"in profile {names}; enable it with --profile {needed[0]} (or COMPOSE_PROFILES)"
    )


_INSTALL_HINT = "install the official docker-compose (brew install docker-compose)"
_OVERRIDE_FILENAMES = (
    "compose.override.yaml",
    "compose.override.yml",
    "docker-compose.override.yaml",
    "docker-compose.override.yml",
)


def _require_builtin_subset_inputs(
    files: list[str],
    profiles: list[str],
    compose_paths: list[str],
    env_files: tuple[str, ...] = (),
) -> None:
    """Refuse inputs only the official normalizer can honour (exit 64).

    Several files, profiles, several env files, ``COMPOSE_ENV_FILES`` (when no
    ``--env-file`` overrides it) and an automatically merged override file all
    change the model, and the built-in parser would silently ignore them.
    """
    if len(compose_paths) > 1:
        raise ShimError(
            "compose: merging several -f files is not supported by the built-in "
            f"parser; {_INSTALL_HINT}",
            64,
        )
    if profiles:
        raise ShimError(
            "compose: --profile and COMPOSE_PROFILES are not supported by the "
            f"built-in parser; {_INSTALL_HINT}",
            64,
        )
    if len(env_files) > 1:
        raise ShimError(
            "compose: merging several --env-file files is not supported by the "
            f"built-in parser; {_INSTALL_HINT}",
            64,
        )
    if not env_files and os.environ.get("COMPOSE_ENV_FILES"):
        raise ShimError(
            "compose: COMPOSE_ENV_FILES is not supported by the built-in "
            f"parser; {_INSTALL_HINT}",
            64,
        )
    if not files:
        directory = os.path.dirname(compose_paths[0])
        for name in _OVERRIDE_FILENAMES:
            if os.path.isfile(os.path.join(directory, name)):
                raise ShimError(
                    f"compose: {name} would be merged by Docker Compose, but the "
                    f"built-in parser ignores it; {_INSTALL_HINT}",
                    64,
                )


def _load_project(
    *,
    files: list[str],
    profiles: list[str],
    project_name: str | None,
    project_dir: str | None,
    env_files: tuple[str, ...],
) -> Project:
    directory = os.path.abspath(project_dir or os.getcwd())
    compose_paths = _find_compose_files(files, directory)
    project_dir_resolved = os.path.abspath(project_dir or os.path.dirname(compose_paths[0]))

    executable = _resolve_normalizer()
    if executable is not None:
        inputs = _normalizer_inputs(
            files, profiles, compose_paths, project_name, project_dir, env_files
        )
        try:
            model, raw = _load_normalized_model(executable, inputs)
        except ShimError as exc:
            hint = _inactive_profile_hint(executable, inputs, str(exc))
            if hint is None:
                raise
            raise ShimError(hint, exc.code) from exc
        name = _sanitize_project_name(model["name"])
        project = Project(name, model, project_dir_resolved)
        project.normalized = raw
        return project

    _require_builtin_subset_inputs(files, profiles, compose_paths, env_files)
    compose_path = compose_paths[0]

    with open(compose_path, encoding="utf-8") as handle:
        text = handle.read()
    try:
        compose_subset.check_subset(text)
    except compose_subset.SubsetError as exc:
        raise ShimError(f"compose: {compose_path}: {exc}", 64)
    raw_doc = parse_yaml(text)
    if not isinstance(raw_doc, dict):
        raise ShimError("compose: top-level document must be a mapping", 64)

    if env_files:
        env = _load_env_file(env_files[0], required=True)
    else:
        env = _load_env_file(os.path.join(project_dir_resolved, ".env"))
    env.update(os.environ)  # shell environment overrides .env, like Docker
    doc = _interpolate(raw_doc, env)

    name = _resolve_project_name(project_name, doc, project_dir_resolved)
    return Project(name, doc, project_dir_resolved)


def _resolve_project_name(explicit: str | None, doc: dict[str, Any], directory: str) -> str:
    if explicit:
        return _sanitize_project_name(explicit)
    env_name = os.environ.get("COMPOSE_PROJECT_NAME")
    if env_name:
        return _sanitize_project_name(env_name)
    if doc.get("name"):
        return _sanitize_project_name(_as_str(doc["name"]))
    return _sanitize_project_name(os.path.basename(directory.rstrip("/")) or "default")


# ========================================================================== #
# Service -> `container run` translation
# ========================================================================== #


def _network_resource(project: Project, key: str) -> compose_resources.Resource:
    """The resolved network for ``key``; an undeclared key is a managed one."""
    found = project.resources.networks.get(key)
    return found or compose_resources.Resource(key, f"{project.name}_{key}", False)


def _volume_resource(project: Project, key: str) -> compose_resources.Resource:
    """The resolved volume for ``key``; an undeclared key is a managed one."""
    found = project.resources.volumes.get(key)
    return found or compose_resources.Resource(key, f"{project.name}_{key}", False)


def _local_bind(
    project: Project, key: str
) -> Optional[compose_resource_compat.LocalBind]:
    """The bind a top-level volume stands for (the ``local`` driver idiom), if any.

    Raises ``MountSpecError`` for a ``driver_opts`` form the shared verdict
    refuses (the gate has already reported it; this keeps the translator from
    guessing). An external volume is never a bind: it names an existing one.
    """
    if not key or _volume_resource(project, key).external:
        return None
    verdict = compose_resource_compat.local_bind(_resource_spec(project.volumes, key))
    if isinstance(verdict, compose_resource_compat.Problem):
        raise MountSpecError(f"volume {key!r}: {verdict.message}")
    if isinstance(verdict, compose_resource_compat.LocalBind):
        return verdict
    return None


def _is_local_bind(project: Project, key: str) -> bool:
    """Whether ``key`` is a local-driver bind; a refused form is not (the gate reports it)."""
    try:
        return _local_bind(project, key) is not None
    except MountSpecError:
        return False


def _compose_port_to_publish(port: Any) -> str:
    """Translate a compose port entry into Apple `-p [ip:]host:container[/proto]`.

    A bare container port (``"6379"``) is published to the identical host
    port, deterministically — Apple has no random-host-port mode.
    """
    spec = _as_str(port)
    proto = ""
    if "/" in spec:
        spec, _, proto = spec.partition("/")
        proto = f"/{proto}"
    parts = spec.split(":")
    if len(parts) == 1:
        return f"0.0.0.0:{parts[0]}:{parts[0]}{proto}"
    if len(parts) == 2:
        host, container = parts
        # IP-less host:container — bind on all interfaces, like Docker.
        return f"0.0.0.0:{host}:{container}{proto}"
    return f"{spec}{proto}"


def _translate_environment(spec_env: Any) -> list[str]:
    args: list[str] = []
    if isinstance(spec_env, dict):
        for key, value in spec_env.items():
            args.extend(["-e", f"{key}={_as_str(value)}"])
    elif isinstance(spec_env, list):
        for entry in spec_env:
            args.extend(["-e", _as_str(entry)])
    return args


@dataclasses.dataclass(frozen=True)
class _PlannedMount:
    """One validated mount: the typed mount and, for a named volume, its Compose key."""

    mount: Mount
    volume_key: Optional[str] = None
    anonymous: Optional[compose_resources.Resource] = None


def _entry_mount(entry: Any, project: Project, service: str = "") -> _PlannedMount:
    """Pure translation of one volume entry (short or long syntax) into a mount.

    Mode and attribute policy lives in :mod:`.mounts`; a named volume's source is
    replaced by the runtime name the project resolved for it, and an entry
    without a source is the service's anonymous volume at its target. Raises
    ``MountSpecError`` for anything the policy or the translator refuses.
    """
    if isinstance(entry, dict):
        mount = mount_from_entry(entry, project.directory)
    else:
        text = _as_str(entry)
        if text.startswith("/") and ":" not in text:
            mount = mount_from_entry({"type": "volume", "target": text}, project.directory)
        elif len(text.split(":")) < 2:
            raise MountSpecError(f"invalid volume entry: {text!r}")
        else:
            mount = parse_volume_spec(text, cwd=project.directory)
    if mount.kind != "volume":
        return _PlannedMount(mount)
    key = mount.source
    if not key:
        if not service:
            raise MountSpecError(f"anonymous volume at {mount.target!r} needs its service")
        volume = compose_resources.anonymous_volume(project.name, service, mount.target)
        return _PlannedMount(dataclasses.replace(mount, source=volume.name), None, volume)
    bind = _local_bind(project, key)
    if bind is not None:
        # `driver_opts: {type: none, o: bind, device: /path}` is Docker's spelling
        # of a bind mount: no runtime volume is created or owned.
        return _PlannedMount(
            Mount("bind", bind.device, mount.target, mount.readonly or bind.read_only)
        )
    return _PlannedMount(
        dataclasses.replace(mount, source=_volume_resource(project, key).name), key
    )


def _service_mounts(service: Service, project: Project) -> list[_PlannedMount]:
    """Every mount of a service, validated without touching the runtime."""
    return [
        _entry_mount(entry, project, service.name)
        for entry in _as_list(service.spec.get("volumes"))
    ]


def _mount_error(service: Service, exc: MountSpecError) -> ShimError:
    return ShimError(f"compose: service {service.name!r}: {exc}", 64)


def _classify_and_translate_volume(
    entry: Any,
    project: Project,
    ensured_volumes: set[str],
    service: str = "",
    image: str = "",
) -> list[str]:
    """Translate one compose volume entry into `container run` args.

    Host-path sources (``./data``, ``/abs``) become bind mounts. Bare names
    are project-scoped Apple **named volumes** (created on demand), mounted
    with the native ``--mount type=volume`` form.
    """
    try:
        planned = _entry_mount(entry, project, service)
    except MountSpecError as exc:
        raise ShimError(f"compose: {exc}", 64)
    return _emit_planned(planned, project, ensured_volumes, service=service, image=image)


def _emit_planned(
    planned: _PlannedMount,
    project: Project,
    ensured_volumes: set[str],
    *,
    service: str = "",
    image: str = "",
) -> list[str]:
    """The only effect of mount translation: create the volume on demand.

    A named volume is created empty; an anonymous one is created and filled
    with the image's content at its target (Docker's copy-up).
    """
    if planned.anonymous is not None:
        volume = planned.anonymous
        if volume.name not in ensured_volumes:
            _ensure_anonymous_volume(volume, planned.mount.target, service, image)
            ensured_volumes.add(volume.name)
        return emit_mount(planned.mount)
    key, resource = planned.volume_key, planned.mount.source
    if key is not None and resource not in ensured_volumes:
        volume = _volume_resource(project, key)
        if volume.managed:
            declared = project.volumes.get(key) if isinstance(project.volumes, dict) else None
            labels = declared.get("labels") if isinstance(declared, dict) else None
            _ensure_named_volume(resource, project.name, labels, key=volume.key)
        ensured_volumes.add(resource)
    return emit_mount(planned.mount)


def _service_network_keys(service: Service) -> list[str]:
    declared = service.spec.get("networks")
    if isinstance(declared, dict):
        return [str(key) for key in declared]
    if isinstance(declared, list):
        return [str(item) for item in declared]
    return ["default"]


def _service_networks(service: Service, project: Project) -> list[str]:
    return [_network_resource(project, key).name for key in _service_network_keys(service)]


def _service_volume_keys(service: Service, project: Optional[Project] = None) -> list[str]:
    """Keys of the named volumes a service mounts (host paths and tmpfs excluded).

    With ``project``, a volume that is only a bind (the local-driver idiom) is
    not a runtime volume and is left out.
    """
    keys: list[str] = []
    for entry in _as_list(service.spec.get("volumes")):
        if isinstance(entry, dict):
            if entry.get("type") not in (None, "volume"):
                continue
            source = _as_str(entry.get("source"))
        else:
            source = _as_str(entry).split(":")[0]
        if source and not is_host_path(source):
            if project is not None and _is_local_bind(project, source):
                continue
            keys.append(source)
    return keys


def _build_run_args(
    service: Service,
    project: Project,
    *,
    image: str,
    ensured_volumes: set[str],
    warned: set[str],
    boot_hosts: list[str] | None = None,
    config_hash: str | None = None,
    image_id: str | None = None,
) -> tuple[list[str], bool]:
    """Translate a service into `container run` args.

    Returns ``(args, wrapped)`` — ``wrapped`` is True when ``boot_hosts``
    lines were baked into a /bin/sh entrypoint prelude (see
    ``_boot_hosts_script``), so the caller can fall back to an unwrapped
    launch if the wrapped one fails to start.
    """
    spec = service.spec
    args: list[str] = ["-d"]

    container_name = service.container_name or f"{project.name}-{service.name}-1"
    args.extend(["--name", container_name])

    # Compose label schema — the entire stateless bookkeeping lives here.
    args.extend(["--label", f"{LABEL_PROJECT}={project.name}"])
    args.extend(["--label", f"{LABEL_SERVICE}={service.name}"])
    args.extend(["--label", f"{LABEL_NUMBER}=1"])
    args.extend(["--label", f"{LABEL_ONEOFF}=False"])
    if config_hash:
        args.extend(["--label", f"{LABEL_CONFIG_HASH}={config_hash}"])
    if image_id:
        args.extend(["--label", f"{LABEL_IMAGE}={image_id}"])
    user_labels = spec.get("labels")
    if isinstance(user_labels, dict):
        for key, value in user_labels.items():
            args.extend(["--label", f"{key}={_as_str(value)}"])
    elif isinstance(user_labels, list):
        for entry in user_labels:
            args.extend(["--label", _as_str(entry)])

    for net in _service_networks(service, project):
        args.extend(["--network", net])

    for dns in _as_list(spec.get("dns")):
        args.extend(["--dns", _as_str(dns)])
    for dns_search in _as_list(spec.get("dns_search")):
        args.extend(["--dns-search", _as_str(dns_search)])
    for dns_opt in _as_list(spec.get("dns_opt")):
        args.extend(["--dns-option", _as_str(dns_opt)])

    if spec.get("platform"):
        args.extend(["--platform", _as_str(spec["platform"])])
    if spec.get("user"):
        args.extend(["--user", _as_str(spec["user"])])
    if spec.get("working_dir"):
        args.extend(["-w", _as_str(spec["working_dir"])])
    if spec.get("read_only") is True:
        args.append("--read-only")
    if spec.get("init") is True:
        args.append("--init")
    if spec.get("tty") is True:
        args.append("-t")
    if spec.get("stdin_open") is True:
        args.append("-i")
    if spec.get("shm_size"):
        args.extend(["--shm-size", _as_str(spec["shm_size"])])

    for cap in _as_list(spec.get("cap_add")):
        args.extend(["--cap-add", _as_str(cap)])
    for cap in _as_list(spec.get("cap_drop")):
        args.extend(["--cap-drop", _as_str(cap)])
    for limit in _ulimit_values(spec.get("ulimits")):
        args.extend(["--ulimit", limit])
    for entry in _as_list(spec.get("tmpfs")):
        args.extend(["--tmpfs", _as_str(entry)])

    cpus, memory = _resource_limits(spec)
    if cpus:
        args.extend(["--cpus", _normalize_cpus(cpus)])
    if memory:
        args.extend(["--memory", _normalize_memory(memory)])

    args.extend(_translate_environment(spec.get("environment")))
    for env_file in _as_list(spec.get("env_file")):
        path = _as_str(env_file)
        resolved = path if os.path.isabs(path) else os.path.join(project.directory, path)
        for key, value in _load_env_file(resolved).items():
            args.extend(["-e", f"{key}={value}"])

    for port in _as_list(spec.get("ports")):
        args.extend(["-p", _compose_port_to_publish(port)])

    try:
        planned_mounts = _service_mounts(service, project)
    except MountSpecError as exc:
        raise _mount_error(service, exc)
    for planned in planned_mounts:
        args.extend(
            _emit_planned(planned, project, ensured_volumes, service=service.name, image=image)
        )

    # Compose entrypoint follows the same string-splitting rules as command.
    entrypoint = _command_list(spec["entrypoint"]) if spec.get("entrypoint") is not None else None
    command = _command_list(spec.get("command"))
    positional: list[str] = []
    wrapped = False
    if boot_hosts:
        argv = _boot_argv(service, image, entrypoint, command)
        if argv:
            args.extend(["--entrypoint", "/bin/sh"])
            positional = ["-c", _boot_hosts_script(boot_hosts), "sh", *argv]
            wrapped = True
        else:
            _warn(
                f"service {service.name!r}: could not resolve the image entrypoint — "
                "dependency names will be injected post-start only"
            )
    if not wrapped:
        if entrypoint:
            args.extend(["--entrypoint", entrypoint[0]])
            positional.extend(entrypoint[1:])
        if command:
            positional.extend(command)

    args.append(image)
    args.extend(positional)
    return args, wrapped


def _ulimit_values(value: Any) -> list[str]:
    """Translate Compose `ulimits` into `<type>=<soft>[:<hard>]` strings.

    Compose defines `ulimits` as a mapping (`nproc: 65535` or
    `nofile: {soft: 20000, hard: 40000}`); a list of `type=value` strings is
    also accepted.
    """
    if isinstance(value, dict):
        out: list[str] = []
        for name, limit in value.items():
            if isinstance(limit, dict):
                soft = _as_str(limit.get("soft"))
                hard = _as_str(limit.get("hard"))
                out.append(f"{name}={soft}:{hard}")
            else:
                out.append(f"{name}={_as_str(limit)}")
        return out
    return [_as_str(item) for item in _as_list(value)]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _command_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_as_str(item) for item in value]
    # Compose splits string commands with shell *lexical* rules — it does NOT
    # wrap them in `sh -c` (that's Dockerfile shell form, a different layer).
    # `command: --log-dir /data` must reach the image ENTRYPOINT as two args.
    return shlex.split(_as_str(value))


def _resource_limits(spec: dict[str, Any]) -> tuple[str, str]:
    cpus = ""
    memory = ""
    deploy = spec.get("deploy")
    if isinstance(deploy, dict):
        limits = (deploy.get("resources") or {}).get("limits") if isinstance(deploy.get("resources"), dict) else None
        if isinstance(limits, dict):
            if limits.get("cpus"):
                cpus = _as_str(limits["cpus"])
            if limits.get("memory"):
                memory = _as_str(limits["memory"])
    if spec.get("cpus"):
        cpus = _as_str(spec["cpus"])
    if spec.get("mem_limit"):
        memory = _as_str(spec["mem_limit"])
    return cpus, memory


def _mount_problems(
    project: Project, services: list[Service], reported: list[str]
) -> list[str]:
    """``REFUSED:`` lines for mounts the translator cannot express.

    The capability gate already reports refused mount options; a problem whose
    text it printed is not repeated. What is left is what only translation
    knows (a comma in a path, a relative target, a malformed entry).
    """
    found: list[str] = []
    for service in services:
        for entry in _as_list(service.spec.get("volumes")):
            try:
                _entry_mount(entry, project, service.name)
            except MountSpecError as exc:
                if any(str(exc) in line for line in reported):
                    continue
                found.append(
                    compose_capabilities.refused(f"service {service.name!r}: volumes: {exc}")
                )
    return found


def _build_secret_problems(project: Project, services: list[Service]) -> list[str]:
    """Structural problems of every ``build.secrets`` entry (pure, no filesystem)."""
    found: list[str] = []
    for service in services:
        build = service.spec.get("build")
        if not isinstance(build, dict) or not build.get("secrets"):
            continue
        _secrets, problems, _notices = compose_build_secrets.resolve(
            service, build.get("secrets"), project.secrets, project.directory
        )
        found.extend(problem.removeprefix("compose: ") for problem in problems)
    return found


def _check_build_secrets(
    project: Project, services: list[Service], *, build: bool, no_build: bool
) -> None:
    """Refuse missing secret files and unset variables before any side effect.

    Only the services that will build are checked: with the image present and no
    ``--build`` Docker never reads the secret either. Read-only (one
    ``image inspect`` per candidate); no value is ever read or printed.
    """
    if no_build:
        return
    problems: list[str] = []
    for service in services:
        spec_build = service.spec.get("build")
        if not isinstance(spec_build, dict) or not spec_build.get("secrets"):
            continue
        if not (build or not _image_exists(_build_tag(service, project))):
            continue
        secrets, _problems, _notices = compose_build_secrets.resolve(
            service, spec_build.get("secrets"), project.secrets, project.directory
        )
        problems.extend(
            compose_build_secrets.check(secrets, os.path.isfile, os.environ, service.name)
        )
    if problems:
        for line in problems:
            print(compose_capabilities.refused(line.removeprefix("compose: ")), file=sys.stderr)
        raise ShimError("compose: nothing was created; fix the problems above", 64)


def _validate_plan(project: Project, services: list[Service]) -> None:
    """Validate every service about to be acted on, before any side effect.

    One pass collects all refusals (capability table, security decisions and
    dependency rules), prints them together as ``REFUSED:`` lines, and raises
    exit 64. Partial applications are printed as ``NOT APPLIED:`` lines before
    anything is provisioned. Nothing here talks to the runtime.
    """
    report = compose_capabilities.validate(
        {"networks": project.networks, "volumes": project.volumes},
        services,
        os.environ,
    )
    problems = [
        compose_capabilities.refused(f"service {f.service!r}: {f.key}: {f.message}")
        for f in report.refusals
    ]
    problems.extend(_mount_problems(project, services, problems))
    problems.extend(
        compose_capabilities.refused(line)
        for line in _build_secret_problems(project, services)
    )
    dependency_notices: list[str] = []
    try:
        dependency_notices = _validate_dependencies(services)
    except ShimError as exc:
        problems.append(compose_capabilities.refused(str(exc)))
    if problems:
        for line in problems:
            print(line, file=sys.stderr)
        raise ShimError("compose: nothing was created; fix the problems above", 64)
    for line in dependency_notices:
        _warn(line)
    for f in report.notices:
        print(
            compose_capabilities.not_applied(f"service {f.service!r}: {f.key}: {f.message}"),
            file=sys.stderr,
        )



def _counts_as_requested(value: Any) -> bool:
    """Whether a compose value counts as requested (not an empty/no-op form).

    The minimal YAML parser can return strings for empty-ish values, so both
    native and string forms are treated as "not requested".
    """
    if value is None or value is False:
        return False
    return value not in ("", [], {}, "false", "False", "0", "-1", 0, -1)


def _validate_dependencies(services: list[Service]) -> list[str]:
    """Reject `service_healthy` depends_on conditions with no usable healthcheck.

    A dependency without an enabled healthcheck would start before it could
    ever report healthy, so the request is refused (all-or-nothing, like
    `_validate_plan`). For an optional entry (``required: false``) that is a
    notice instead, returned for the caller to print. Dependencies not
    defined in the selected set are ignored, matching the rest of the file.
    """
    notices: list[str] = []
    for service in services:
        try:
            compose_health.parse_healthcheck(service.spec.get("healthcheck"))
        except ValueError as exc:
            raise ShimError(
                f"compose: service {service.name!r}: invalid healthcheck: {exc}", 64
            )
    by_name = {service.name: service for service in services}
    for service in services:
        for dep, condition in service.depends_conditions.items():
            if condition != "service_healthy":
                continue
            target = by_name.get(dep)
            if target is None:
                continue
            try:
                check = compose_health.parse_healthcheck(target.spec.get("healthcheck"))
            except ValueError as exc:
                raise ShimError(f"compose: service {dep!r}: invalid healthcheck: {exc}", 64)
            if check is None or check.disabled:
                message = (
                    f"service {service.name!r} depends_on {dep!r} with "
                    f"condition service_healthy, but {dep!r} has no enabled healthcheck"
                )
                if not service.depends_required.get(dep, True):
                    notices.append(f"{message}; not waiting, because the dependency is optional")
                    continue
                raise ShimError(f"compose: {message}", 64)
    return notices


def _warn_ungated_healthchecks(
    services: list[Service], gated: set[str], wait: bool
) -> None:
    """Say so when a healthcheck is declared but nothing here will evaluate it."""
    if wait:
        return
    for service in services:
        check = compose_health.parse_healthcheck(service.spec.get("healthcheck"))
        if check is None or check.disabled or service.name in gated:
            continue
        _warn(
            f"service {service.name!r}: 'healthcheck' is only evaluated to gate "
            "depends_on service_healthy or 'up --wait'; there is no continuous "
            "monitoring and no restart on failure"
        )


def _container_state(name: str) -> str:
    """Return ``"running"``, ``"exited"`` or ``"starting"`` (a missing container counts as exited)."""
    result = _run_container_capture(["inspect", name])
    if result.returncode != 0:
        return "exited"
    import json

    try:
        data = json.loads(result.stdout or "[]")
    except ValueError:
        return "exited"
    item = data[0] if isinstance(data, list) and len(data) == 1 else None
    if not isinstance(item, dict):
        return "exited"
    state = (item.get("status") or {}).get("state")
    if state == "running":
        return "running"
    if state in ("stopped", "exited", "dead", None, ""):
        return "exited"
    return "starting"  # any transitional state: keep waiting


def _log_tail(name: str, lines: int = 40) -> str:
    result = _run_container_capture(["logs", name])
    return "\n".join((result.stdout or "").rstrip("\n").splitlines()[-lines:])


def _exec_with_timeout(timeout: float):
    """Build an ``exec_fn`` for ``run_check_once`` that enforces the check timeout."""
    import subprocess

    def run(argv: list[str]) -> Any:
        try:
            return _run_container_capture(argv, timeout=timeout)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                argv, 1, "", f"healthcheck timed out after {timeout:g}s"
            )

    return run


def _check_completed(service: Service, name: str, result: Any) -> int:
    """Report the outcome of a foreground ``run``; return 0 only for exit code 0."""
    if result.returncode == 0:
        print(f"{name} exited with code 0")
        return 0
    if _run_container_capture(["inspect", name]).returncode != 0:
        # The container was never created: this is a launch error, not an exit code.
        sys.stderr.write(result.stderr or result.stdout)
        return result.returncode
    message = [
        f"compose: service {service.name!r} ({name}) exited with code {result.returncode}"
    ]
    logs = _log_tail(name)
    if logs:
        message.append("last log lines:\n" + logs)
    sys.stderr.write("\n".join(message) + "\n")
    return 1


def _wait_started_services(
    project: Project,
    started: list[Service],
    completed: set[str],
    healthy_waited: set[str],
    wait_timeout: Optional[float],
) -> int:
    """``up --wait``: every started service is running and, with a healthcheck, healthy.

    Services that are ``service_completed_successfully`` dependencies exit by
    design and are skipped; services already gated on ``service_healthy`` while
    creating are not waited on twice.
    """
    for service in started:
        if service.name in completed:
            continue
        name = service.container_name or f"{project.name}-{service.name}-1"
        if service.name not in healthy_waited:
            check = compose_health.parse_healthcheck(service.spec.get("healthcheck"))
            if check is not None and not check.disabled:
                rc = _wait_service_healthy(service, name, wait_timeout)
                if rc != 0:
                    return rc
                continue
        deadline = time.monotonic() + (wait_timeout if wait_timeout is not None else 30.0)
        state = _container_state(name)
        while state == "starting" and time.monotonic() < deadline:
            time.sleep(0.2)
            state = _container_state(name)
        if state != "running":
            reason = "is not running" if state == "exited" else "did not start before the timeout"
            message = [f"compose: service {service.name!r} ({name}) {reason}"]
            logs = _log_tail(name)
            if logs:
                message.append("last log lines:\n" + logs)
            sys.stderr.write("\n".join(message) + "\n")
            return 1
    return 0


def _wait_service_healthy(
    service: Service, name: str, wait_timeout: Optional[float]
) -> int:
    """Block until ``name`` passes its healthcheck; print diagnostics and return 1 otherwise."""
    check = compose_health.parse_healthcheck(service.spec.get("healthcheck"))
    if check is None or check.disabled:  # _validate_dependencies refuses this earlier
        return 0
    print(f"Waiting for {name} to become healthy ...")
    exec_fn = _exec_with_timeout(check.timeout)
    outcome = compose_health.wait_healthy(
        lambda: compose_health.run_check_once(exec_fn, name, check),
        check,
        lambda: _container_state(name),
        time.monotonic,
        time.sleep,
        timeout=wait_timeout,
    )
    if outcome.status == "healthy":
        print(f"{name} is healthy")
        return 0
    reason = {
        "unhealthy": f"became unhealthy after {check.retries} failed checks",
        "exited": "exited before becoming healthy",
        "timeout": "did not become healthy before the timeout",
    }[outcome.status]
    message = [f"compose: service {service.name!r} ({name}) {reason}"]
    if outcome.last_output:
        message.append(f"last check output: {outcome.last_output}")
    logs = _log_tail(name)
    if logs:
        message.append("last log lines:\n" + logs)
    sys.stderr.write("\n".join(message) + "\n")
    return 1


# ========================================================================== #
# Apple-store queries (the only place project state is read from)
# ========================================================================== #


def _project_containers(project: str) -> list[dict[str, Any]]:
    code, rows, err = _load_container_rows(all_containers=True)
    if code != 0:
        raise ShimError(err.strip() or "compose: could not list containers", code)
    members = [row for row in rows if (row.get("labels") or {}).get(LABEL_PROJECT) == project]
    members.sort(key=lambda r: (r.get("labels") or {}).get(LABEL_SERVICE, ""))
    return members


def _ordered_project_containers(
    project_name: str,
    *,
    definition: Project | None,
    only: list[str],
    reverse: bool = False,
) -> list[dict[str, Any]]:
    """Return one labeled container per service in a deterministic order.

    A compose file supplies dependency order when one is available. Label-only
    operation remains deterministic without it by sorting service names. Scale
    is deliberately out of scope, so duplicate service rows or a container
    number other than 1 fail instead of being selected arbitrarily.
    """
    by_service: dict[str, dict[str, Any]] = {}
    for row in _project_containers(project_name):
        labels = row.get("labels") or {}
        service = labels.get(LABEL_SERVICE)
        number = labels.get(LABEL_NUMBER)
        if not service:
            raise ShimError(
                f"compose: project container {row.get('name') or row.get('id')} "
                f"is missing {LABEL_SERVICE}",
                64,
            )
        if number not in (None, "1") or service in by_service:
            raise ShimError(
                f"compose: service {service!r} has multiple containers; "
                "scaling is not supported",
                64,
            )
        by_service[service] = row

    selected = set(only) if only else set(by_service)
    missing = [service for service in only if service not in by_service]
    if missing:
        raise ShimError(
            f"compose: no container found for service(s): {', '.join(missing)}",
            1,
        )

    names: list[str] = []
    if definition is not None:
        names.extend(
            service.name
            for service in definition.topo_sorted()
            if service.name in selected
        )
    names.extend(sorted(selected - set(names)))
    if reverse:
        names.reverse()
    return [by_service[name] for name in names]


def _container_ident(row: dict[str, Any]) -> str:
    ident = row.get("id") or row.get("name")
    if not ident:
        raise ShimError("compose: project container has no usable identity", 64)
    return str(ident)


def refresh_hosts_for_started_containers(idents: list[str]) -> None:
    """Refresh service discovery for projects containing started containers.

    Bare lifecycle commands have no compose file, so reconstruct the affected
    projects exclusively from Docker Compose labels already stored by Apple.
    Current network attachments remain the source of truth for every injected
    address; no shim-owned state is read or written.
    """
    if not idents:
        return
    code, rows, err = _load_container_rows(all_containers=True)
    if code != 0:
        raise ShimError(err.strip() or "compose: could not list containers", code)

    def matches(row: dict[str, Any], ident: str) -> bool:
        if str(row.get("name") or "") == ident:
            return True
        return any(
            str(value).startswith(ident)
            for value in (row.get("id"), row.get("apple_id"))
            if value
        )

    affected = {
        str((row.get("labels") or {}).get(LABEL_PROJECT))
        for row in rows
        if any(matches(row, ident) for ident in idents)
        and (row.get("labels") or {}).get(LABEL_PROJECT)
    }
    for project_name in sorted(affected):
        services: dict[str, dict[str, str]] = {}
        for row in rows:
            labels = row.get("labels") or {}
            if labels.get(LABEL_PROJECT) != project_name:
                continue
            service = labels.get(LABEL_SERVICE)
            number = labels.get(LABEL_NUMBER)
            name = row.get("name") or row.get("id")
            if not service or not name:
                raise ShimError(
                    f"compose: project container {name or '<unknown>'} is missing "
                    f"{LABEL_SERVICE}",
                    64,
                )
            if number not in (None, "1") or service in services:
                raise ShimError(
                    f"compose: service {service!r} has multiple containers; "
                    "scaling is not supported",
                    64,
                )
            services[str(service)] = {"container_name": str(name)}
        project = Project(project_name, {"services": services}, os.getcwd())
        _inject_etc_hosts(project, project.topo_sorted())


def _all_projects() -> dict[str, list[dict[str, Any]]]:
    code, rows, err = _load_container_rows(all_containers=True)
    if code != 0:
        raise ShimError(err.strip() or "compose: could not list containers", code)
    projects: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        name = (row.get("labels") or {}).get(LABEL_PROJECT)
        if name:
            projects.setdefault(name, []).append(row)
    return projects


def _load_labeled_resources(family: str, project: str) -> list[str]:
    """Return names of `network`/`volume` resources owned by ``project``."""
    result = _run_container_capture([family, "list", "--format", "json"])
    if result.returncode != 0:
        return []
    import json

    try:
        data = json.loads(result.stdout or "[]")
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    owned: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        config = item.get("configuration") if isinstance(item.get("configuration"), dict) else item
        labels = config.get("labels") if isinstance(config, dict) else None
        name = (config.get("name") if isinstance(config, dict) else None) or item.get("id")
        if isinstance(labels, dict) and labels.get(LABEL_PROJECT) == project and name:
            owned.append(str(name))
    return owned


NetworkAttachment = tuple[str, str, Optional[str]]  # (network, ipv4, gateway)


def _container_networks(name: str) -> list[NetworkAttachment]:
    """Return all IPv4 network attachments for a container, in runtime order.

    Apple `container inspect` reports addresses under ``status.networks[]``.
    The order matters: the first network supplies the container's DNS/gateway,
    while later networks may still be the right service-to-service path. Keep
    every attachment so /etc/hosts injection can pick an address on a network
    shared by the target container and each peer.
    """
    result = _run_container_capture(["inspect", name])
    if result.returncode != 0:
        return []
    import json

    try:
        data = json.loads(result.stdout or "[]")
    except ValueError:
        return []
    item = data[0] if isinstance(data, list) and len(data) == 1 else None
    if not isinstance(item, dict):
        return []
    networks = (item.get("status") or {}).get("networks") or []
    attachments: list[NetworkAttachment] = []
    for net in networks:
        if not isinstance(net, dict):
            continue
        network = net.get("network")
        addr = net.get("ipv4Address")
        if not network or not addr:
            continue
        gateway = net.get("ipv4Gateway")
        ip = str(addr).split("/")[0]
        gw = str(gateway).split("/")[0] if gateway else None
        attachments.append((str(network), ip, gw))
    return attachments


def _container_net(name: str) -> tuple[str | None, str | None]:
    """Return ``(ipv4, gateway)`` for a container's first network, if any.

    Kept as a small compatibility helper for call sites that only need the
    container's primary address/gateway.
    """
    networks = _container_networks(name)
    if not networks:
        return None, None
    _network, ip, gateway = networks[0]
    return ip, gateway


# ========================================================================== #
# Resource provisioning
# ========================================================================== #


def _list_resources(family: str) -> dict[str, dict[str, str]]:
    """Name -> labels of every runtime ``network``/``volume``; read-only.

    A runtime that cannot answer is an error, never "nothing exists": the
    caller would otherwise create objects on top of a failed check.
    """
    import json

    result = _run_container_capture([family, "list", "--format", "json"])
    if result.returncode != 0:
        raise ShimError(
            f"compose: cannot check {family}s: {(result.stderr or result.stdout).strip()}",
            result.returncode,
        )
    try:
        data = json.loads(result.stdout or "[]")
    except ValueError as exc:
        raise ShimError(f"compose: cannot read the {family} list: {exc}", 1) from exc
    found: dict[str, dict[str, str]] = {}
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        config = item.get("configuration") if isinstance(item.get("configuration"), dict) else item
        name = config.get("name") or item.get("id")
        labels = config.get("labels")
        if name:
            found[str(name)] = (
                {str(k): str(v) for k, v in labels.items()} if isinstance(labels, dict) else {}
            )
    return found


def _check_resources(project: Project, services: list[Service]) -> list[str]:
    """Verify every resource the planned services use before anything is created.

    External resources must exist (exit 1 otherwise). A managed name that
    already exists must not belong to another project (exit 1); one with no
    Compose label is reused. Returns the notices for ``cmd_up`` to print.
    Only ``list`` calls are made, so nothing is created or changed here.
    """
    uses: dict[str, dict[str, tuple[compose_resources.Resource, str]]] = {
        "network": {},
        "volume": {},
    }
    for service in services:
        for key in _service_network_keys(service):
            uses["network"].setdefault(key, (_network_resource(project, key), service.name))
        for key in _service_volume_keys(service, project):
            uses["volume"].setdefault(key, (_volume_resource(project, key), service.name))
    uses["network"].setdefault("default", (_network_resource(project, "default"), ""))
    planned_services = {service.name for service in services}
    for volume in project.resources.anonymous:
        owner = volume.labels.get(compose_resources.LABEL_SERVICE, "")
        if owner in planned_services:
            uses["volume"].setdefault(volume.name, (volume, owner))

    notices: list[str] = []
    for family, used in uses.items():
        if not used:
            continue
        existing = _list_resources(family)
        for key, (resource, service_name) in used.items():
            labels = existing.get(resource.name)
            if resource.external:
                if labels is None:
                    where = f"service {service_name!r}: " if service_name else ""
                    detail = (
                        f'external volume "{resource.name}" not found'
                        if family == "volume"
                        else f'network {resource.name} declared as external, but could not be found'
                    )
                    raise ShimError(f"compose: {where}{detail}", 1)
                notices.extend(_legacy_copy_notices(project, family, key, resource, existing))
            elif labels is not None:
                owner = labels.get(LABEL_PROJECT)
                if owner and owner != project.name:
                    raise ShimError(
                        f'compose: {family} "{resource.name}" belongs to project '
                        f'"{owner}", not "{project.name}"',
                        1,
                    )
                if not owner:
                    notices.append(
                        f'{family} "{resource.name}" already exists without a Compose '
                        "label; reusing it, and down -v will not remove it"
                    )
    return notices


def _legacy_copy_notices(
    project: Project,
    family: str,
    key: str,
    resource: compose_resources.Resource,
    existing: dict[str, dict[str, str]],
) -> list[str]:
    """Earlier versions created ``<project>_<key>`` for an external key; report it."""
    legacy = f"{project.name}_{key}"
    labels = existing.get(legacy)
    if legacy == resource.name or labels is None or labels.get(LABEL_PROJECT) != project.name:
        return []
    return [
        f'{family} "{legacy}" is a copy left by an earlier version; the external '
        f'{family} "{resource.name}" is used instead, and the copy is left in place'
    ]


def _ensure_network(project: Project, network: str) -> None:
    declared = _resource_spec(project.networks, network)
    resource_info = _network_resource(project, network)
    if resource_info.external:
        return  # never created or removed by this project
    resource = resource_info.name
    existing = _run_container_capture(["network", "inspect", resource])
    if existing.returncode == 0:
        return
    spec = declared
    args = [
        "network", "create",
        "--label", f"{LABEL_PROJECT}={project.name}",
        "--label", f"{compose_resources.LABEL_NETWORK}={network}",
    ]
    args.extend(_resource_label_args(spec.get("labels")))
    if spec.get("internal") is True:
        args.append("--internal")
    args.append(resource)
    result = _run_container_capture(args)
    if result.returncode != 0:
        raise ShimError(
            f"compose: failed to create network {resource}: "
            f"{(result.stderr or result.stdout).strip()}",
            result.returncode,
        )


# Copy-up: anonymous volumes start with what the image has at the target. The
# target is a positional parameter so it needs no quoting; a path that is not
# in the image leaves the volume empty, which is what Docker does too.
_SEED_DEST = "/shim-dest"
_SEED_SCRIPT = 'if [ -d "$1" ]; then cp -a "$1"/. ' + _SEED_DEST + '/; fi'


def _ensure_anonymous_volume(
    volume: compose_resources.Resource, target: str, service: str, image: str
) -> None:
    """Create the anonymous volume once and fill it from ``image`` (design D5).

    An existing volume is reused as it is: its data is never overwritten. When
    the image's content cannot be copied the new volume is removed and the
    command fails before the service starts.
    """
    if _run_container_capture(["volume", "inspect", volume.name]).returncode == 0:
        return
    label_args: list[str] = []
    for name, value in volume.labels.items():
        label_args.extend(["--label", f"{name}={value}"])
    created = _run_container_capture(["volume", "create", *label_args, volume.name])
    if created.returncode != 0:
        raise ShimError(
            f"compose: failed to create volume {volume.name}: "
            f"{(created.stderr or created.stdout).strip()}",
            created.returncode,
        )
    seeded = _run_container_capture([
        "run", "--rm", "--name", f"{volume.name}-seed",
        "--entrypoint", "/bin/sh",
        "--mount", f"type=volume,source={volume.name},target={_SEED_DEST}",
        image, "-c", _SEED_SCRIPT, "sh", target,
    ])
    if seeded.returncode != 0:
        _run_container_capture(["volume", "rm", volume.name])
        detail = (seeded.stderr or seeded.stdout).strip()
        raise ShimError(
            f"compose: service {service!r}: could not copy the content of {image} at "
            f"{target} into the new anonymous volume ({detail}); the volume was removed. "
            "The image needs /bin/sh and cp, or use a named volume",
            1,
        )


def _resource_spec(declared: Any, key: str) -> dict[str, Any]:
    """The top-level spec of a network or volume, ``{}`` when absent or null."""
    if isinstance(declared, dict) and isinstance(declared.get(key), dict):
        return declared[key]
    return {}


def _resource_label_args(labels: Any) -> list[str]:
    """``--label`` arguments for the user labels of a network or volume."""
    if isinstance(labels, dict):
        labels = [f"{key}={_as_str(value)}" for key, value in labels.items()]
    args: list[str] = []
    for label in _as_list(labels):
        args.extend(["--label", _as_str(label)])
    return args


def _ensure_named_volume(
    resource: str, project: str, labels: Any = None, key: str | None = None
) -> None:
    existing = _run_container_capture(["volume", "inspect", resource])
    if existing.returncode == 0:
        return
    key_label = ["--label", f"{compose_resources.LABEL_VOLUME}={key}"] if key else []
    result = _run_container_capture(
        [
            "volume", "create", "--label", f"{LABEL_PROJECT}={project}",
            *key_label, *_resource_label_args(labels), resource,
        ]
    )
    if result.returncode != 0:
        raise ShimError(
            f"compose: failed to create volume {resource}: "
            f"{(result.stderr or result.stdout).strip()}",
            result.returncode,
        )


# ========================================================================== #
# Service discovery via each container's own /etc/hosts
# ========================================================================== #


# Docker Desktop auto-publishes these names (both → the host) into every
# container's /etc/hosts on macOS/Windows. Apple `container` does not, and has
# no `--add-host` flag, so we reproduce it via the same per-container injection.
_HOST_ALIASES = "host.docker.internal gateway.docker.internal"


def _cp_hosts_fallback(name: str, lines: list[str], managed: list[str]) -> bool:
    """Inject hosts lines via `container cp` when the image has no shell.

    `container cp` rides the guest agent, so it works on running distroless
    containers where `exec sh` cannot (e.g. cloudflare/cloudflared). Copy the
    container's /etc/hosts out, drop lines mentioning any ``managed`` name
    (same replace-not-append semantics as the exec path), append the fresh
    ``lines``, copy it back. Only suitable post-start — but a shell-less image
    can't take the boot-time wrapper either, so this is its one injection
    path. Returns True on success.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, "hosts")
        if _run_container_capture(["cp", f"{name}:/etc/hosts", local]).returncode != 0:
            return False
        try:
            with open(local, encoding="utf-8") as fh:
                current = fh.read().splitlines()
        except OSError:
            return False
        managed_words = set(managed)
        kept = [l for l in current if not (set(l.split()) & managed_words)]
        with open(local, "w", encoding="utf-8") as fh:
            fh.write("\n".join(kept + lines) + "\n")
        return _run_container_capture(["cp", local, f"{name}:/etc/hosts"]).returncode == 0


def _select_shared_attachment(
    target_network_order: list[str], peer_networks: list[NetworkAttachment]
) -> tuple[str | None, str | None]:
    """Return ``(peer_ip, peer_gateway)`` on the first network shared by target.

    Docker's embedded DNS only resolves service names on networks shared by
    the caller and callee. Apple `container` has no embedded DNS, so the shim's
    /etc/hosts entries must follow the same rule. This is especially important
    for a service attached to ``[egress, private]``: its first address can reach
    the Internet, but a private-only peer must resolve it to the private address.
    """
    by_network = {network: (ip, gateway) for network, ip, gateway in peer_networks}
    for network in target_network_order:
        hit = by_network.get(network)
        if hit:
            return hit
    return None, None


def _boot_hosts_lines(
    service: Service,
    project: Project,
    nets_by_service: dict[str, list[NetworkAttachment]],
) -> list[str]:
    """/etc/hosts lines a dependent service needs before its entrypoint runs.

    Dependencies started earlier in this `up` are in ``nets_by_service``; a
    dependency excluded by a partial `up <service>` may still be running from
    a previous `up`, so fall back to inspecting it live. Services without
    ``depends_on`` return [] — for them post-start injection is the whole
    story, as before.
    """
    if not service.depends_on or service.spec.get(BOOT_HOSTS_KEY) is False:
        return []
    target_networks = _service_networks(service, project)
    lines: list[str] = []
    gateway: str | None = None
    for dep in dict.fromkeys(service.depends_on):
        dep_networks = nets_by_service.get(dep)
        if dep_networks is None:
            dep_service = project.services.get(dep)
            if dep_service is None:
                continue  # depends_on a service not defined here — ignore, like Docker
            dep_name = dep_service.container_name or f"{project.name}-{dep}-1"
            dep_networks = _container_networks(dep_name)
        ip, dep_gateway = _select_shared_attachment(target_networks, dep_networks)
        gateway = gateway or dep_gateway
        if ip:
            lines.append(f"{ip} {dep}")
        else:
            _warn(
                f"service {service.name!r}: dependency {dep!r} has no shared "
                "address yet — "
                f"{dep!r} will only resolve after post-start injection"
            )
    if lines and gateway:
        lines.append(f"{gateway} {_HOST_ALIASES}")
    return lines


def _boot_hosts_script(lines: list[str]) -> str:
    """A /bin/sh prelude: write ``lines`` into /etc/hosts, then exec the real argv.

    The real argv rides in as ``"$@"`` so it never needs quoting into the
    script. Append failures (read-only fs, non-root USER, no grep) must not
    stop the container from booting — hence the blanket ``|| true``.
    """
    entries = " ".join(shlex.quote(line) for line in lines)
    return (
        "{ for l in " + entries + "; do "
        'grep -qxF "$l" /etc/hosts || printf \'%s\\n\' "$l" >> /etc/hosts; '
        "done; } 2>/dev/null || true; "
        'exec "$@"'
    )


def _boot_argv(
    service: Service, image: str, entrypoint: list[str] | None, command: list[str]
) -> list[str] | None:
    """The exact argv the container would run unwrapped, or None if unknowable.

    Mirrors Docker precedence: a compose ``entrypoint:`` override also
    discards the image CMD; otherwise image ENTRYPOINT + (compose command,
    or the image CMD when no command is given).
    """
    if entrypoint is not None:
        return (entrypoint + command) or None
    platform_hint = _as_str(service.spec["platform"]) if service.spec.get("platform") else None
    defaults = _image_default_argv(image, platform_hint)
    if defaults is None:
        return None
    image_entrypoint, image_cmd = defaults
    return (image_entrypoint + (command or image_cmd)) or None


def _image_default_argv(image: str, platform_hint: str | None) -> tuple[list[str], list[str]] | None:
    """(ENTRYPOINT, CMD) from the image config, or None if not inspectable.

    `container image inspect` returns one entry per platform variant; pick
    the one matching the requested platform (or the host architecture).
    """
    result = _run_container_capture(["image", "inspect", image])
    if result.returncode != 0:
        return None
    import json

    try:
        data = json.loads(result.stdout or "[]")
    except ValueError:
        return None
    item = data[0] if isinstance(data, list) and len(data) == 1 else None
    if not isinstance(item, dict):
        return None
    variants = [v for v in item.get("variants") or [] if isinstance(v, dict)]
    if not variants:
        return None
    if platform_hint and "/" in platform_hint:
        want_arch: str | None = platform_hint.split("/")[1]
    else:
        import platform as host_platform

        machine = host_platform.machine().lower()
        want_arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(machine, machine)
    chosen = variants[0]
    for variant in variants:
        if (variant.get("platform") or {}).get("architecture") == want_arch:
            chosen = variant
            break
    config = (chosen.get("config") or {}).get("config") or {}
    image_entrypoint = config.get("Entrypoint") or []
    image_cmd = config.get("Cmd") or []
    if not isinstance(image_entrypoint, list) or not isinstance(image_cmd, list):
        return None
    return [_as_str(x) for x in image_entrypoint], [_as_str(x) for x in image_cmd]


def _hosts_rewrite_script(lines: list[str], managed: list[str], path: str = "/etc/hosts") -> str:
    """Shell script that makes ``path`` carry exactly ``lines`` for the managed names.

    Idempotent: every existing line mentioning a managed name is dropped, then
    the fresh lines are appended, so running it twice (or after an address
    change) never leaves a duplicate or a stale address. ``grep -v`` exiting 1
    (nothing left) is fine — the appends still run.
    """
    pattern = (
        "(^|[[:space:]])("
        + "|".join(re.escape(word) for word in managed)
        + ")([[:space:]]|$)"
    )
    appends = "".join(
        f"; printf '%s\\n' {shlex.quote(line)} >> \"$tmp\"" for line in lines
    )
    quoted_path = shlex.quote(path)
    return (
        'tmp="$(mktemp)"; '
        f'grep -vE {shlex.quote(pattern)} {quoted_path} > "$tmp"'
        + appends
        + f'; cat "$tmp" > {quoted_path}; rm -f "$tmp"'
    )


def _inject_etc_hosts(
    project: Project,
    services: list[Service],
    net_info: dict[str, list[NetworkAttachment]] | None = None,
) -> None:
    """Populate each container's own /etc/hosts with peer + host-gateway names.

    Two things are injected per container, via `container exec`:

    1. **Peer service names** (``<ip> <service>``) so services resolve each
       other by name — only meaningful when the project has ≥2 services. When
       services have multiple networks, each receiver gets the peer address on
       a network they actually share, mirroring Docker's embedded DNS.
    2. **``host.docker.internal`` / ``gateway.docker.internal``** → the
       container's first-network gateway, which on Apple `container` *is* the
       macOS host. This mirrors Docker Desktop, so images that dial the host by
       that name (a very common Docker-on-Mac assumption) work unchanged.
       Injected even for single-service projects.

    All writes land in **each container's ephemeral /etc/hosts**, discarded on
    removal — the macOS host's /etc/hosts is never read or written. Managed
    names are **replaced, not appended**: a partial ``up <service>`` gives the
    recreated container a new IP, and the resolver takes the *first* matching
    line, so stale entries in its peers must be dropped, not shadowed. If the
    `exec` fails (no shell in the image), a `container cp` rewrite is tried;
    a container that is not running (partial up) is skipped silently.

    Callers must pass the **whole project's services** — peer names come from
    every running container, not just the ones a partial `up` touched.
    ``net_info`` carries addresses already inspected by the caller (keyed by
    container name); anything missing is inspected here.
    """
    info: dict[str, list[NetworkAttachment]] = dict(net_info or {})
    named: list[tuple[Service, str]] = []
    for service in services:
        name = service.container_name or f"{project.name}-{service.name}-1"
        if name not in info:
            info[name] = _container_networks(name)
        named.append((service, name))

    for service, name in named:
        networks = info[name]
        if not networks:
            continue  # not running — nothing to write into
        target_networks = [network for network, _ip, _gateway in networks]
        gateway = networks[0][2]
        lines: list[str] = []
        if len(named) >= 2:
            for peer_service, peer_name in named:
                peer_ip, _peer_gateway = _select_shared_attachment(
                    target_networks, info.get(peer_name, [])
                )
                if peer_ip:
                    lines.append(f"{peer_ip} {peer_service.name}")
        if gateway:
            lines.append(f"{gateway} {_HOST_ALIASES}")
        if not lines:
            continue
        managed = (
            [svc.name for svc, _name in named] if len(named) >= 2 else []
        ) + _HOST_ALIASES.split()
        script = _hosts_rewrite_script(lines, managed)
        result = _run_container_capture(["exec", name, "sh", "-c", script])
        if result.returncode != 0 and not _cp_hosts_fallback(name, lines, managed):
            _warn(
                f"could not write /etc/hosts in {name} (no shell in image, or "
                "container exited before injection) — service/host-gateway "
                "names may be unavailable there"
            )


# ========================================================================== #
# Verbs
# ========================================================================== #


def _remove_containers(rows: list[dict[str, Any]]) -> int:
    rc = 0
    for row in rows:
        ident = row.get("id") or row.get("name")
        if not ident:
            continue
        stop = _run_container_capture(["stop", ident])
        if stop.returncode != 0 and row.get("state") == "running":
            sys.stderr.write(stop.stderr or stop.stdout)
            rc = stop.returncode
        remove = _run_container_capture(["rm", ident])
        if remove.returncode != 0:
            sys.stderr.write(remove.stderr or remove.stdout)
            rc = remove.returncode
        else:
            print(f"Removed {row.get('name') or ident}")
    return rc


def _recreate_reason(
    row: Optional[dict[str, Any]], wanted_hash: str, wanted_image: str, force: bool
) -> str:
    """Why an existing container would be recreated (for the progress line)."""
    if row is None:
        return ""
    if force:
        return "forced"
    labels = row.get("labels") or {}
    if compose_reconcile.LABEL_CONFIG_HASH not in labels:
        return "created without a configuration label"
    if labels.get(compose_reconcile.LABEL_IMAGE) != wanted_image:
        return "image changed"
    return "configuration changed"


def _handle_orphans(
    project: Project, rows: list[dict[str, Any]], *, remove: bool
) -> None:
    """Containers of this project whose service is no longer in the file."""
    orphans = [
        row
        for row in rows
        if (row.get("labels") or {}).get(LABEL_SERVICE) not in project.services
    ]
    if not orphans:
        return
    names = ", ".join(str(row.get("name") or row.get("id")) for row in orphans)
    if remove:
        print(f"Removing orphan containers: {names}")
        _remove_containers(orphans)
    else:
        _warn(
            f"found orphan containers for this project ({names}); "
            "use --remove-orphans to remove them"
        )


def _start_existing(service: Service, name: str, completes: bool) -> int:
    """Start a stopped container whose configuration is unchanged."""
    if completes:
        # A one-shot dependency runs again; `start -a` returns its exit code.
        print(f"Waiting for {name} to complete ...")
        return _check_completed(service, name, _run_container_capture(["start", "-a", name]))
    result = _run_container_capture(["start", name])
    if result.returncode != 0:
        sys.stderr.write(f"compose: service {service.name!r} failed to start\n")
        sys.stderr.write(result.stderr or result.stdout)
    return result.returncode


def _create_service(
    service: Service,
    project: Project,
    *,
    image: str,
    image_id: str,
    config_hash: str,
    name: str,
    ensured_volumes: set[str],
    warned: set[str],
    nets_by_service: dict[str, list[NetworkAttachment]],
    completes: bool,
) -> int:
    """`container run` for a service; return 0 or the failing exit code."""
    boot_hosts = _boot_hosts_lines(service, project, nets_by_service)
    run_args, wrapped = _build_run_args(
        service,
        project,
        image=image,
        ensured_volumes=ensured_volumes,
        warned=warned,
        boot_hosts=boot_hosts,
        config_hash=config_hash,
        image_id=image_id,
    )
    # A `service_completed_successfully` dependency runs in the foreground:
    # `container run` returns the container's own exit code (the runtime
    # does not expose it through `inspect`), which is what the condition needs.
    if completes:
        print(f"Waiting for {name} to complete ...")
        fg_args = run_args[1:] if run_args[:1] == ["-d"] else run_args
        return _check_completed(service, name, _run_container_capture(["run", *fg_args]))
    result = _run_container_capture(["run", *run_args])
    if result.returncode != 0 and wrapped:
        # Most likely no /bin/sh in the image — relaunch unwrapped and
        # leave name resolution to post-start injection, as before.
        _warn(
            f"service {service.name!r}: boot-time hosts wrapper failed to "
            "start — retrying without it"
        )
        _run_container_capture(["rm", name])
        run_args, _ = _build_run_args(
            service,
            project,
            image=image,
            ensured_volumes=ensured_volumes,
            warned=warned,
            config_hash=config_hash,
            image_id=image_id,
        )
        result = _run_container_capture(["run", *run_args])
    if result.returncode != 0:
        sys.stderr.write(f"compose: service {service.name!r} failed to start\n")
        sys.stderr.write(result.stderr or result.stdout)
    return result.returncode


def _print_dry_run(project: Project, services: list[Service]) -> None:
    """Print what ``up`` would create, from the same ordered list ``up`` uses."""
    networks: dict[str, compose_resources.Resource] = {}
    volumes: dict[str, compose_resources.Resource] = {}
    for key in ["default", *(k for svc in services for k in _service_network_keys(svc))]:
        networks.setdefault(key, _network_resource(project, key))
    planned = {service.name for service in services}
    for service in services:
        for key in _service_volume_keys(service, project):
            volumes.setdefault(key, _volume_resource(project, key))
    for volume in project.resources.anonymous:
        if volume.labels.get(compose_resources.LABEL_SERVICE, "") in planned:
            volumes.setdefault(volume.name, volume)

    def lines(kind: str, items: dict[str, compose_resources.Resource]) -> None:
        for resource in items.values():
            note = " (external, must exist)" if resource.external else ""
            print(f"{kind} {resource.name}{note}")

    print(f"Dry run of project {project.name}: nothing was created.")
    lines("Network", networks)
    lines("Volume", volumes)
    for service in services:
        name = service.container_name or f"{project.name}-{service.name}-1"
        print(f"Container {name} (service {service.name})")
    print(
        "Dry run: only the configuration was checked; image availability, "
        "build results and start-up health are not evaluated."
    )


def cmd_up(
    project: Project,
    *,
    detach: bool,
    build: bool,
    only: list[str],
    no_build: bool,
    wait_timeout: Optional[float] = None,
    wait: bool = False,
    force_recreate: bool = False,
    no_recreate: bool = False,
    remove_orphans: bool = False,
    no_deps: bool = False,
    dry_run: bool = False,
) -> int:
    # The plan is the single source of truth for what this `up` acts on: the
    # set that is validated and checked is the set that is created.
    service_plan = compose_plan.plan(project, only, no_deps=no_deps)
    services = [project.services[name] for name in service_plan.ordered]
    for notice in service_plan.notices:
        _warn(notice)

    _validate_plan(project, services)
    _check_build_secrets(project, services, build=build, no_build=no_build)
    detach = detach or wait  # --wait implies --detach

    # Read-only: external resources must exist and managed names must be ours,
    # before the first network, volume or container is created.
    for notice in _check_resources(project, services):
        _warn(notice)
    if dry_run:
        _print_dry_run(project, services)
        return 0

    # Provision declared networks (record specs for ensure-on-attach), defaulting
    # to the implicit project network every service joins.
    _ensure_network(project, "default")
    referenced: set[str] = {"default"}
    for service in services:
        declared = service.spec.get("networks")
        if isinstance(declared, dict):
            referenced.update(str(k) for k in declared)
        elif isinstance(declared, list):
            referenced.update(str(k) for k in declared)
    for net in referenced:
        _ensure_network(project, net)

    # Reconcile instead of clearing: containers whose configuration and image
    # are unchanged stay untouched, stopped ones are started, changed ones are
    # recreated. State lives in the labels written at creation time.
    existing_rows = _project_containers(project.name)
    by_service: dict[str, dict[str, Any]] = {}
    for row in existing_rows:
        label = (row.get("labels") or {}).get(LABEL_SERVICE)
        if label and label not in by_service:
            by_service[str(label)] = row
    _handle_orphans(project, existing_rows, remove=remove_orphans)

    ensured_volumes: set[str] = set()
    warned: set[str] = set()
    started: list[Service] = []
    recreated: set[str] = set()
    net_info: dict[str, list[NetworkAttachment]] = {}
    nets_by_service: dict[str, list[NetworkAttachment]] = {}
    depended_on = {dep for svc in services for dep in svc.depends_on}
    needs_healthy = {
        dep
        for svc in services
        for dep, condition in svc.depends_conditions.items()
        if condition == "service_healthy"
    }
    # A gate only softens when every dependent that waits on it marked it optional.
    optional_gates = {
        dep
        for dep in needs_healthy
        if not any(
            svc.depends_required.get(dep, True)
            for svc in services
            if svc.depends_conditions.get(dep) == "service_healthy"
        )
    }
    _warn_ungated_healthchecks(services, needs_healthy, wait)
    needs_completed = {
        dep
        for svc in services
        for dep, condition in svc.depends_conditions.items()
        if condition == "service_completed_successfully"
    }
    healthy_waited: set[str] = set()
    # Resolve (and build) every image before touching any container, so a
    # missing image or failed build cannot leave the project half changed.
    plans: list[tuple[Service, str, str, str]] = []
    for service in services:
        image = _resolve_image(service, project, build=build, no_build=no_build)
        image_id = _image_id(image)
        plans.append((service, image, image_id, compose_reconcile.config_hash(service.spec, image_id)))
    for service, image, image_id, config_hash in plans:
        name = service.container_name or f"{project.name}-{service.name}-1"
        row = by_service.get(service.name)
        action = compose_reconcile.decide(
            row, config_hash, image_id, force_recreate, no_recreate
        )
        reason = _recreate_reason(row, config_hash, image_id, force_recreate)
        if action in ("keep", "start") and not no_recreate and recreated & set(service.depends_on):
            # A dependency got a new container (and address): this one baked
            # the old address into its boot-time hosts, so it is recreated too.
            action, reason = "recreate", "a dependency was recreated"
        completes = service.name in needs_completed
        if action == "keep":
            print(f"{name} is up to date")
        elif action == "start":
            print(f"Starting {name} ...")
            rc = _start_existing(service, name, completes)
            if rc != 0:
                return rc
        else:
            if action == "recreate":
                print(f"Recreating {name} ({reason}) ...")
                _remove_containers([row])  # type: ignore[list-item]
                recreated.add(service.name)
            else:
                print(f"Creating {name} ...")
            rc = _create_service(
                service,
                project,
                image=image,
                image_id=image_id,
                config_hash=config_hash,
                name=name,
                ensured_volumes=ensured_volumes,
                warned=warned,
                nets_by_service=nets_by_service,
                completes=completes,
            )
            if rc != 0:
                return rc
        started.append(service)
        if completes:
            net_info[name] = []
            continue
        # Learn this container's address now so later dependents can bake it
        # into their boot-time /etc/hosts (retry briefly — the address can lag
        # `run` returning by a beat, and a dependent needs it).
        networks = _container_networks(name)
        if not networks and service.name in depended_on:
            for _ in range(3):
                time.sleep(0.3)
                networks = _container_networks(name)
                if networks:
                    break
        net_info[name] = networks
        if networks:
            nets_by_service[service.name] = networks
        if service.name in needs_healthy:
            rc = _wait_service_healthy(service, name, wait_timeout)
            if rc != 0 and service.name in optional_gates:
                _warn(
                    f"optional dependency {service.name!r} did not become healthy; "
                    "continuing because it is not required"
                )
            elif rc != 0:
                return rc
            healthy_waited.add(service.name)  # waited once; `--wait` must not wait again

    # Whole project, not just `started`: a partial `up <svc>` must give the
    # fresh container ALL peer names, and rewrite the recreated service's new
    # address into every already-running peer.
    _inject_etc_hosts(project, project.topo_sorted(), net_info)

    if wait:
        rc = _wait_started_services(
            project, started, needs_completed, healthy_waited, wait_timeout
        )
        if rc != 0:
            return rc
    if detach:
        return 0
    # Foreground: stream the logs of every started service until interrupted.
    return _stream_logs(project, started, follow=True)


def _removable_names(
    family: str, project_name: str, declared: dict[str, compose_resources.Resource]
) -> list[str]:
    """Names of the declared, managed ``family`` resources this project labels."""
    labelled = {
        name: labels.get(LABEL_PROJECT, "") for name, labels in _list_resources(family).items()
    }
    return [r.name for r in compose_resources.removable(declared.values(), labelled, project_name)]


def _runtime_volumes(project: Project) -> dict[str, compose_resources.Resource]:
    """The volumes the project can own at runtime: declared ones and anonymous ones.

    Local-driver binds are only paths, so they are left out.
    """
    volumes = {
        key: volume
        for key, volume in project.resources.volumes.items()
        if not _is_local_bind(project, key)
    }
    volumes.update({volume.name: volume for volume in project.resources.anonymous})
    return volumes


def cmd_down(
    project_name: str,
    *,
    remove_volumes: bool,
    only: list[str],
    project: Project | None = None,
) -> int:
    """Remove the project's containers, then the networks (and volumes) it owns.

    Ownership is the current model AND the project label (design D3): without
    the model, volumes are never removed (``-v`` is refused up front, before
    anything is touched) and networks fall back to the label alone.
    """
    if remove_volumes and project is None:
        return _die(
            "compose: down -v needs the Compose file to know which volumes the project "
            "owns; run it where the file is (or pass -f), or run down without -v",
            64,
        )
    rows = _project_containers(project_name)
    if only:
        wanted = set(only)
        rows = [r for r in rows if (r.get("labels") or {}).get(LABEL_SERVICE) in wanted]
    if not rows and not only:
        _warn(f"no containers found for project {project_name!r}")
    rc = _remove_containers(rows)

    if only:
        return rc  # partial down never tears down shared infrastructure

    try:
        if project is not None:
            networks = _removable_names("network", project_name, project.resources.networks)
        else:
            networks = _load_labeled_resources("network", project_name)
        volumes = (
            _removable_names("volume", project_name, _runtime_volumes(project))
            if remove_volumes and project is not None
            else []
        )
    except ShimError as exc:
        sys.stderr.write(f"{exc}\n")
        return rc or exc.code
    for network in networks:
        result = _run_container_capture(["network", "rm", network])
        if result.returncode == 0:
            print(f"Removed network {network}")
        else:
            sys.stderr.write(result.stderr or result.stdout)
            rc = rc or result.returncode

    for volume in volumes:
        result = _run_container_capture(["volume", "rm", volume])
        if result.returncode == 0:
            print(f"Removed volume {volume}")
        else:
            sys.stderr.write(result.stderr or result.stdout)
            rc = rc or result.returncode
    return rc


def cmd_ps(
    project_name: str,
    *,
    quiet: bool = False,
    show_all: bool = False,
    output_format: str = "table",
    statuses: list[str] | None = None,
    services_only: bool = False,
    only: list[str] | None = None,
) -> int:
    rows = _project_containers(project_name)
    if not show_all:
        rows = [r for r in rows if r.get("state") == "running"]
    if statuses:
        wanted_statuses = {status.lower() for status in statuses}
        rows = [r for r in rows if str(r.get("state") or "").lower() in wanted_statuses]
    if only:
        wanted_services = set(only)
        rows = [r for r in rows if (r.get("labels") or {}).get(LABEL_SERVICE) in wanted_services]
    if services_only:
        output = "".join(
            f"{service}\n"
            for service in sorted({(row.get("labels") or {}).get(LABEL_SERVICE, "") for row in rows})
            if service
        )
        sys.stdout.write(output)
        return 0
    if quiet:
        sys.stdout.write("".join(f"{row.get('id', '')}\n" for row in rows))
        return 0
    if output_format == "json":
        missing = _exited_without_exit_code(rows)
        if missing:
            # Apple container 1.5.0's list/inspect JSON exposes no numeric
            # exit code, so an exited row cannot fill Compose's integer
            # ExitCode. Refuse the JSON listing instead of printing 0; the
            # default table, -q and --services remain available.
            raise ShimError(
                "compose ps --format json: " + ", ".join(missing)
                + " has exited, but Apple container 1.5.0 exposes no numeric exit code, "
                "so Compose's ExitCode cannot be reported honestly; refusing to print a "
                "fabricated value. Use the default table, -q, or --services instead.",
                64,
            )
        output = _render_ps_json(rows)
    else:
        output = _render_ps_table(rows)
    sys.stdout.write(output)
    return 0


def cmd_logs(project: Project | None, project_name: str, *, follow: bool, only: list[str]) -> int:
    rows = _project_containers(project_name)
    if only:
        wanted = set(only)
        rows = [r for r in rows if (r.get("labels") or {}).get(LABEL_SERVICE) in wanted]
    if not rows:
        return _die(f"compose: no running containers for project {project_name!r}", 1)
    services = [
        _Stub((row.get("labels") or {}).get(LABEL_SERVICE, ""), row.get("name", ""))
        for row in rows
    ]
    return _stream_logs(None, services, follow=follow)


def cmd_ls() -> int:
    projects = _all_projects()
    print("NAME\tSTATUS")
    for name in sorted(projects):
        rows = projects[name]
        running = sum(1 for row in rows if row.get("state") == "running")
        print(f"{name}\trunning({running})")
    return 0


def cmd_config(project: Project, *, quiet: bool = False) -> int:
    import json

    services = {svc.name: svc.spec for svc in project.topo_sorted()}  # rejects cycles
    if quiet:
        return 0
    if project.normalized is not None:
        print(json.dumps(project.normalized, indent=2))
        return 0
    rendered = {
        "name": project.name,
        "services": services,
        "networks": project.networks,
        "volumes": project.volumes,
    }
    print(json.dumps(rendered, indent=2, default=_as_str))
    return 0


def cmd_version() -> int:
    """Report which normalizer is active (the Compose release when official)."""
    executable = _resolve_normalizer()
    if executable is None:
        print("normalizer: built-in subset")
    else:
        version = compose_normalizer.executable_version(
            executable, _run_normalizer, os.environ
        )
        print(f"normalizer: official {version}")
    return 0


def cmd_build(project: Project, *, only: list[str]) -> int:
    services = project.topo_sorted()
    if only:
        wanted = set(only)
        services = [svc for svc in services if svc.name in wanted]
    _validate_plan(project, services)
    _check_build_secrets(project, services, build=True, no_build=False)
    built = 0
    for service in services:
        if "build" not in service.spec:
            continue
        image = _resolve_image(service, project, build=True, no_build=False)
        print(f"Built {image} for service {service.name}")
        built += 1
    if built == 0:
        _warn("no services with a 'build:' section")
    return 0


def _selected_services(project: Project, only: list[str]) -> list[Service]:
    services = project.topo_sorted()
    if not only:
        return services
    missing = [name for name in only if name not in project.services]
    if missing:
        raise ShimError(
            f"compose: no such service(s): {', '.join(missing)}",
            1,
        )
    wanted = set(only)
    return [service for service in services if service.name in wanted]


def cmd_pull(project: Project, *, only: list[str]) -> int:
    rc = 0
    for service in _selected_services(project, only):
        image = _as_str(service.spec.get("image"))
        if not image:
            _warn(f"service {service.name!r} has no image to pull")
            continue
        result = docker_image(["pull", image])
        if result != 0:
            rc = result
    return rc


def cmd_start(
    project_name: str, *, definition: Project | None, only: list[str]
) -> int:
    rows = _ordered_project_containers(
        project_name,
        definition=definition,
        only=only,
    )
    ids = [_container_ident(row) for row in rows if row.get("state") != "running"]
    return docker_start(ids) if ids else 0


def cmd_stop(
    project_name: str,
    *,
    definition: Project | None,
    only: list[str],
    timeout: str | None,
) -> int:
    rows = _ordered_project_containers(
        project_name,
        definition=definition,
        only=only,
        reverse=True,
    )
    ids = [_container_ident(row) for row in rows if row.get("state") == "running"]
    if not ids:
        return 0
    args: list[str] = []
    if timeout is not None:
        args.extend(["--timeout", timeout])
    args.extend(ids)
    return docker_stop(args)


def cmd_restart(
    project_name: str,
    *,
    definition: Project | None,
    only: list[str],
    timeout: str | None,
) -> int:
    forward = _ordered_project_containers(
        project_name,
        definition=definition,
        only=only,
    )
    reverse = list(reversed(forward))
    running = [
        _container_ident(row) for row in reverse if row.get("state") == "running"
    ]
    if running:
        stop_args: list[str] = []
        if timeout is not None:
            stop_args.extend(["--timeout", timeout])
        stop_args.extend(running)
        rc = docker_stop(stop_args)
        if rc != 0:
            return rc
    ids = [_container_ident(row) for row in forward]
    return docker_start(ids) if ids else 0


def cmd_rm(
    project_name: str,
    *,
    definition: Project | None,
    only: list[str],
    stop: bool,
) -> int:
    rows = _ordered_project_containers(
        project_name,
        definition=definition,
        only=only,
        reverse=True,
    )
    running = [row for row in rows if row.get("state") == "running"]
    if running and stop:
        rc = docker_stop([_container_ident(row) for row in running])
        if rc != 0:
            return rc
    elif running:
        names = ", ".join(
            str(row.get("labels", {}).get(LABEL_SERVICE) or _container_ident(row))
            for row in running
        )
        _warn(f"running service container(s) not removed: {names}; use --stop")
    removable = rows if stop else [row for row in rows if row.get("state") != "running"]
    ids = [_container_ident(row) for row in removable]
    return docker_rm(ids) if ids else 0


def cmd_exec(
    project_name: str,
    *,
    argv: list[str],
) -> int:
    detach = False
    no_tty = False
    interactive = True
    forwarded: list[str] = []
    service: str | None = None
    command: list[str] = []

    i = 0
    while i < len(argv):
        raw = argv[i]
        if raw == "--":
            i += 1
            if i < len(argv):
                service = argv[i]
                command = argv[i + 1 :]
            break
        if not raw.startswith("-"):
            service = raw
            command = argv[i + 1 :]
            break
        if raw in ("-d", "--detach"):
            detach = True
            i += 1
            continue
        if raw in ("-T", "--no-TTY"):
            no_tty = True
            i += 1
            continue
        if raw in ("-i", "--interactive"):
            interactive = True
            i += 1
            continue
        if raw == "--interactive=false":
            interactive = False
            i += 1
            continue

        key, inline, canonical = _compose_exec_value_option(raw)
        if key is not None:
            if inline is None:
                if i + 1 >= len(argv):
                    raise ShimError(f"compose: {raw} requires a value", 64)
                inline = argv[i + 1]
                i += 2
            else:
                i += 1
            forwarded.extend([canonical, inline])
            continue
        raise ShimError(f"compose: unsupported exec option: {raw}", 64)

    if service is None:
        raise ShimError("compose: exec requires a service", 64)
    if not command:
        raise ShimError("compose: exec requires a command", 64)
    [row] = _ordered_project_containers(
        project_name,
        definition=None,
        only=[service],
    )

    args: list[str] = []
    if detach:
        args.append("--detach")
    else:
        if interactive:
            args.append("-i")
        if not no_tty:
            args.append("-t")
    args.extend(forwarded)
    args.append(_container_ident(row))
    args.extend(command)
    return docker_exec(args)


def _compose_exec_value_option(
    raw: str,
) -> tuple[str | None, str | None, str]:
    long_names = {
        "--env": "-e",
        "--env-file": "--env-file",
        "--user": "--user",
        "--workdir": "-w",
    }
    short_names = {"-e": "-e", "-u": "--user", "-w": "-w"}
    if raw.startswith("--"):
        key, sep, value = raw.partition("=")
        canonical = long_names.get(key)
        if canonical is not None:
            return key, value if sep else None, canonical
        return None, None, ""
    key = raw[:2]
    canonical = short_names.get(key)
    if canonical is None:
        return None, None, ""
    return key, raw[2:] or None, canonical


# ========================================================================== #
# Image resolution & build
# ========================================================================== #


def _build_tag(service: Service, project: Project) -> str:
    return _as_str(service.spec.get("image")) or f"{project.name}-{service.name}"


def _resolve_image(service: Service, project: Project, *, build: bool, no_build: bool) -> str:
    spec = service.spec
    if "build" in spec and not no_build:
        tag = _build_tag(service, project)
        if build or not _image_exists(tag):
            _run_build(service, project, tag)
        return tag
    image = _as_str(spec.get("image"))
    if not image:
        raise ShimError(f"compose: service {service.name!r} has neither 'image' nor 'build'", 64)
    return image


def _image_exists(tag: str) -> bool:
    result = _run_container_capture(["image", "inspect", tag])
    return result.returncode == 0


def _image_id(image: str) -> str:
    """The runtime's ID (digest) of a local image, or ``""`` when not inspectable."""
    result = _run_container_capture(["image", "inspect", image])
    if result.returncode != 0:
        return ""
    import json

    try:
        data = json.loads(result.stdout or "[]")
    except ValueError:
        return ""
    item = data[0] if isinstance(data, list) and len(data) == 1 else None
    if not isinstance(item, dict):
        return ""
    return str(item.get("id") or "")


def _build_secret_args(service: Service, project: Project, build: dict[str, Any]) -> list[str]:
    """``--secret`` arguments for ``build.secrets``; the shim never reads the files."""
    secrets, problems, notices = compose_build_secrets.resolve(
        service, build.get("secrets"), project.secrets, project.directory
    )
    if problems:
        raise ShimError("\n".join(problems), 64)
    for line in notices:
        print(line, file=sys.stderr)
    return compose_build_secrets.to_args(secrets)


def _run_build(service: Service, project: Project, tag: str) -> None:
    build = service.spec["build"]
    if isinstance(build, str):
        context = build
        build = {}
    elif isinstance(build, dict):
        context = _as_str(build.get("context") or ".")
    else:
        raise ShimError(f"compose: service {service.name!r} has an invalid 'build'", 64)

    context_path = context if os.path.isabs(context) else os.path.join(project.directory, context)
    # Express the Compose keys as the equivalent `docker build` arguments and
    # translate them with the same code `docker build` uses.
    argv = ["--tag", tag]
    dockerfile = build.get("dockerfile")
    if dockerfile:
        df = _as_str(dockerfile)
        argv.extend(["--file", df if os.path.isabs(df) else os.path.join(context_path, df)])
    raw_args = build.get("args") or {}
    for key, value in (raw_args.items() if isinstance(raw_args, dict) else ()):
        argv.extend(["--build-arg", f"{key}={_as_str(value)}"])
    if build.get("target"):
        argv.extend(["--target", _as_str(build["target"])])
    labels = build.get("labels") or {}
    if isinstance(labels, dict):
        labels = [f"{key}={_as_str(value)}" for key, value in labels.items()]
    for label in _as_list(labels):
        argv.extend(["--label", _as_str(label)])
    if build.get("no_cache") is True:
        argv.append("--no-cache")
    if build.get("pull") is True:
        argv.append("--pull")
    platforms = build.get("platforms")
    if platforms:
        argv.extend(["--platform", ",".join(_as_str(p) for p in _as_list(platforms))])
    elif service.spec.get("platform"):
        argv.extend(["--platform", _as_str(service.spec["platform"])])
    argv.extend(_build_secret_args(service, project, build))
    if build.get("ssh"):
        argv.extend(["--ssh", "default"])
    argv.append(context_path)

    translated = translate_build_argv(argv)
    assert translated is not None
    opts, context_arg = translated
    args = ["build", *opts, context_arg]

    print(f"Building {tag} for service {service.name} ...")
    result = _run_container_passthrough(args)
    if result != 0:
        raise ShimError(f"compose: build failed for service {service.name!r}", result)


# ========================================================================== #
# Log streaming
# ========================================================================== #


class _Stub:
    """Minimal service-like object for log streaming of existing containers."""

    def __init__(self, name: str, container_name: str):
        self.name = name
        self._container_name = container_name

    @property
    def container_name(self) -> str:
        return self._container_name


def _stream_logs(project: Project | None, services: list[Any], *, follow: bool) -> int:
    targets: list[tuple[str, str]] = []
    for service in services:
        if isinstance(service, _Stub):
            name = service.container_name
        else:
            name = service.container_name or f"{project.name}-{service.name}-1"  # type: ignore[union-attr]
        targets.append((service.name, name))

    if not targets:
        return 0
    if len(targets) == 1 or not follow:
        rc = 0
        for label, name in targets:
            args = ["logs"]
            if follow:
                args.append("--follow")
            args.append(name)
            result = _run_container_passthrough(args)
            rc = rc or result
        return rc

    # Multiple services + follow: one thread per container, prefixed output.
    threads: list[threading.Thread] = []
    for label, name in targets:
        thread = threading.Thread(target=_follow_prefixed, args=(label, name), daemon=True)
        thread.start()
        threads.append(thread)
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        return 130
    return 0


def _follow_prefixed(label: str, name: str) -> None:
    import subprocess

    proc = subprocess.Popen(
        [_container_bin(), "logs", "--follow", name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(f"{label}  | {line}")
        sys.stdout.flush()


# ========================================================================== #
# Entry point
# ========================================================================== #


def _warn(message: str) -> None:
    print(f"docker-for-apple-container: compose: {message}", file=sys.stderr)


_USAGE = (
    "docker compose [-f FILE] [-p NAME] "
    "<up|down|ps|logs|build|pull|exec|start|stop|restart|rm|config|ls|version> [options]"
)


def _apply_compose_environment(
    files: list[str], profiles: list[str], environ: Any
) -> tuple[list[str], list[str]]:
    """Fold ``COMPOSE_FILE`` and ``COMPOSE_PROFILES`` into the parsed flags.

    ``COMPOSE_FILE`` only applies when no ``-f`` was given and is split on
    ``COMPOSE_PATH_SEPARATOR`` (default ``os.pathsep``); ``COMPOSE_PROFILES``
    is comma-separated and adds to the ``--profile`` values.
    """
    if not files:
        raw = environ.get("COMPOSE_FILE", "")
        separator = environ.get("COMPOSE_PATH_SEPARATOR") or os.pathsep
        files = [item for item in raw.split(separator) if item]
    extra = [item.strip() for item in environ.get("COMPOSE_PROFILES", "").split(",")]
    return list(files), list(profiles) + [item for item in extra if item]


# Global options that take a value, as `--opt value` or `--opt=value`, mapped
# to their long name (the value is checked for emptiness under that name).
_GLOBAL_VALUE_OPTIONS = {
    "-f": "--file",
    "--file": "--file",
    "--profile": "--profile",
    "-p": "--project-name",
    "--project-name": "--project-name",
    "--project-directory": "--project-directory",
    "--env-file": "--env-file",
}


def _global_option(argv: list[str], index: int) -> tuple[str, str, int] | None:
    """Read the global option at ``index``: ``(long name, value, next index)``.

    Returns None when the argument is not a global option that takes a value.
    A missing or empty value exits 64 naming the option.
    """
    arg = argv[index]
    if arg in _GLOBAL_VALUE_OPTIONS:
        option = _GLOBAL_VALUE_OPTIONS[arg]
        value, following = _take(argv, index, arg)
    else:
        name, equals, value = arg.partition("=")
        if not equals or not name.startswith("--") or name not in _GLOBAL_VALUE_OPTIONS:
            return None
        option, following = _GLOBAL_VALUE_OPTIONS[name], index + 1
    if not value:
        raise ShimError(f"compose: {option} requires a non-empty value", 64)
    return option, value, following


def main(argv: list[str]) -> int:
    """Parse `docker compose` global flags, then dispatch the subcommand."""
    files: list[str] = []
    profiles: list[str] = []
    project_name: str | None = None
    project_dir: str | None = None
    env_files: list[str] = []
    dry_run = False

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print(_USAGE)
            return 0
        if arg == "--dry-run":
            dry_run = True
            i += 1
            continue
        try:
            parsed = _global_option(argv, i)
        except ShimError as exc:
            return _die(str(exc), exc.code)
        if parsed is None:
            break
        option, value, i = parsed
        if option == "--file":
            files.append(value)
        elif option == "--profile":
            profiles.append(value)
        elif option == "--project-name":
            project_name = value
        elif option == "--project-directory":
            project_dir = value
        else:
            env_files.append(value)

    rest = argv[i:]
    if not rest:
        print(_USAGE)
        return 0
    sub, sub_args = rest[0], rest[1:]
    files, profiles = _apply_compose_environment(files, profiles, os.environ)

    try:
        return _dispatch(
            sub,
            sub_args,
            files=files,
            profiles=profiles,
            project_name=project_name,
            project_dir=project_dir,
            env_files=tuple(env_files),
            dry_run=dry_run,
        )
    except ShimError as exc:
        return _die(str(exc), exc.code)
    except MountSpecError as exc:  # a mount error that escaped validation: no traceback
        return _die(f"compose: {exc}", 64)


def _dispatch(
    sub: str,
    sub_args: list[str],
    *,
    files: list[str],
    profiles: list[str],
    project_name: str | None,
    project_dir: str | None,
    env_files: tuple[str, ...],
    dry_run: bool = False,
) -> int:
    if dry_run and sub != "up":
        raise ShimError(f"compose: --dry-run is only supported with 'up', not '{sub}'", 64)

    def project() -> Project:
        return _load_project(
            files=files,
            profiles=profiles,
            project_name=project_name,
            project_dir=project_dir,
            env_files=env_files,
        )

    def optional_project() -> Project | None:
        directory = os.path.abspath(project_dir or os.getcwd())
        try:
            _find_compose_files(files, directory)
        except ShimError:
            if files:
                raise
            return None
        return project()

    def resolved_name() -> str:
        # Label-only verbs need only the project name; avoid requiring a file.
        if project_name:
            return _sanitize_project_name(project_name)
        env_name = os.environ.get("COMPOSE_PROJECT_NAME")
        if env_name:
            return _sanitize_project_name(env_name)
        try:
            return project().name
        except ShimError:
            directory = os.path.abspath(project_dir or os.getcwd())
            return _sanitize_project_name(os.path.basename(directory.rstrip("/")) or "default")

    if sub == "up":
        wait_timeout, up_args = _extract_wait_timeout(sub_args)
        opts = _parse_flags(
            up_args,
            bools={
                "-d", "--detach", "--build", "--no-build", "--wait",
                "--force-recreate", "--no-recreate", "--remove-orphans", "--no-deps",
            },
        )
        if opts.flag("--force-recreate") and opts.flag("--no-recreate"):
            raise ShimError("compose: --force-recreate and --no-recreate are incompatible", 64)
        return cmd_up(
            project(),
            wait=opts.flag("--wait"),
            force_recreate=opts.flag("--force-recreate"),
            no_recreate=opts.flag("--no-recreate"),
            remove_orphans=opts.flag("--remove-orphans"),
            no_deps=opts.flag("--no-deps"),
            detach=opts.flag("-d") or opts.flag("--detach"),
            build=opts.flag("--build"),
            no_build=opts.flag("--no-build"),
            only=opts.positionals,
            wait_timeout=wait_timeout,
            dry_run=dry_run,
        )
    if sub == "down":
        opts = _parse_flags(sub_args, bools={"-v", "--volumes"})
        remove_volumes = opts.flag("-v") or opts.flag("--volumes")
        model: Project | None
        try:
            model = optional_project()
        except ShimError:
            if remove_volumes:
                raise  # ownership needs a model that loads
            model = None  # a broken file must not block a plain teardown
        return cmd_down(
            model.name if model is not None else resolved_name(),
            remove_volumes=remove_volumes,
            only=opts.positionals,
            project=model,
        )
    if sub == "ps":
        ps_flags: set[str] = set()
        ps_positionals: list[str] = []
        ps_statuses: list[str] = []
        output_format = "table"
        i = 0
        while i < len(sub_args):
            arg = sub_args[i]
            if arg in {"-q", "--quiet", "-a", "--all", "--services"}:
                ps_flags.add(arg)
                i += 1
                continue
            if arg in {"--format", "-f", "--status"}:
                value, i = _take(sub_args, i, arg)
                if arg == "--status":
                    ps_statuses.append(value)
                else:
                    output_format = value
                continue
            if arg.startswith("--format="):
                output_format = arg.split("=", 1)[1]
                i += 1
                continue
            if arg.startswith("--status="):
                ps_statuses.append(arg.split("=", 1)[1])
                i += 1
                continue
            if arg.startswith("-"):
                raise ShimError(f"compose: unsupported option: {arg}", 64)
            ps_positionals.append(arg)
            i += 1
        if not output_format:
            raise ShimError("compose: --format requires a value", 64)
        if "{{" in output_format or "}}" in output_format:
            raise ShimError("compose: Go-template formats are unsupported", 64)
        if output_format not in {"json", "table"}:
            raise ShimError(f"compose: unsupported --format: {output_format}", 64)
        valid_statuses = {"created", "running", "exited", "paused", "restarting", "removing", "dead"}
        invalid_statuses = [status for status in ps_statuses if status.lower() not in valid_statuses]
        if invalid_statuses:
            raise ShimError(f"compose: invalid --status: {invalid_statuses[0]}", 64)
        return cmd_ps(
            resolved_name(),
            quiet="-q" in ps_flags or "--quiet" in ps_flags,
            show_all="-a" in ps_flags or "--all" in ps_flags,
            output_format=output_format,
            statuses=ps_statuses,
            services_only="--services" in ps_flags,
            only=ps_positionals,
        )
    if sub == "logs":
        opts = _parse_flags(sub_args, bools={"-f", "--follow"})
        return cmd_logs(
            None,
            resolved_name(),
            follow=opts.flag("-f") or opts.flag("--follow"),
            only=opts.positionals,
        )
    if sub == "build":
        opts = _parse_flags(sub_args, bools=set())
        return cmd_build(project(), only=opts.positionals)
    if sub == "pull":
        opts = _parse_flags(sub_args, bools=set())
        return cmd_pull(project(), only=opts.positionals)
    if sub == "exec":
        return cmd_exec(resolved_name(), argv=sub_args)
    if sub in {"start", "stop", "restart", "rm"}:
        definition = optional_project()
        name = definition.name if definition is not None else resolved_name()
        if sub == "start":
            opts = _parse_flags(sub_args, bools=set())
            return cmd_start(
                name,
                definition=definition,
                only=opts.positionals,
            )
        if sub == "stop":
            timeout, services = _parse_timeout_services(sub_args)
            return cmd_stop(
                name,
                definition=definition,
                only=services,
                timeout=timeout,
            )
        if sub == "restart":
            timeout, services = _parse_timeout_services(sub_args)
            return cmd_restart(
                name,
                definition=definition,
                only=services,
                timeout=timeout,
            )
        opts = _parse_flags(
            sub_args,
            bools={"-f", "--force", "-s", "--stop", "-fs", "-sf"},
        )
        return cmd_rm(
            name,
            definition=definition,
            only=opts.positionals,
            stop=(
                opts.flag("-s")
                or opts.flag("--stop")
                or opts.flag("-fs")
                or opts.flag("-sf")
            ),
        )
    if sub == "config":
        opts = _parse_flags(sub_args, bools={"-q", "--quiet"})
        return cmd_config(project(), quiet=opts.flag("-q") or opts.flag("--quiet"))
    if sub == "version":
        return cmd_version()
    if sub == "ls":
        return cmd_ls()
    return _die(f"compose: unsupported subcommand: {sub}", 64)


# --------------------------------------------------------------------------- #
# Tiny flag parser for subcommands (boolean flags + positional service names).
# --------------------------------------------------------------------------- #


class _Flags:
    def __init__(self, present: set[str], positionals: list[str]):
        self._present = present
        self.positionals = positionals

    def flag(self, name: str) -> bool:
        return name in self._present


def _extract_wait_timeout(argv: list[str]) -> tuple[Optional[float], list[str]]:
    """Pull ``--wait-timeout <seconds>`` (or ``=``) out of ``argv``."""
    timeout: Optional[float] = None
    rest: list[str] = []
    i = 0
    while i < len(argv):
        raw = argv[i]
        if raw == "--wait-timeout":
            value, i = _take(argv, i, raw)
        elif raw.startswith("--wait-timeout="):
            value, i = raw.split("=", 1)[1], i + 1
        else:
            rest.append(raw)
            i += 1
            continue
        try:
            timeout = float(value)
        except ValueError:
            timeout = -1.0
        if not (0 < timeout < float("inf")):  # also rejects nan
            raise ShimError(
                f"compose: --wait-timeout needs a positive number of seconds, got {value!r}", 64
            )
    return timeout, rest


def _parse_flags(argv: list[str], *, bools: set[str]) -> _Flags:
    present: set[str] = set()
    positionals: list[str] = []
    for arg in argv:
        if arg in bools:
            present.add(arg)
        elif arg.startswith("-"):
            raise ShimError(f"compose: unsupported option: {arg}", 64)
        else:
            positionals.append(arg)
    return _Flags(present, positionals)


def _parse_timeout_services(argv: list[str]) -> tuple[str | None, list[str]]:
    timeout: str | None = None
    services: list[str] = []
    i = 0
    while i < len(argv):
        raw = argv[i]
        if raw in ("-t", "--timeout"):
            timeout, i = _take(argv, i, raw)
            continue
        if raw.startswith("--timeout="):
            timeout = raw.split("=", 1)[1]
            i += 1
            continue
        if raw.startswith("-t") and raw != "-t":
            timeout = raw[2:]
            i += 1
            continue
        if raw.startswith("-"):
            raise ShimError(f"compose: unsupported option: {raw}", 64)
        services.append(raw)
        i += 1
    return timeout, services


def _take(argv: list[str], index: int, opt: str) -> tuple[str, int]:
    if index + 1 >= len(argv):
        raise ShimError(f"compose: {opt} requires a value", 64)
    return argv[index + 1], index + 2
