"""Docker CLI compatibility shim over Apple container."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, distributions, version as _pkg_version
from pathlib import Path
import re


def _distribution_name() -> str | None:
    """Find this package's distribution name without duplicating project metadata."""
    package_file = Path(__file__).resolve()
    for parent in package_file.parents:
        pyproject = parent / "pyproject.toml"
        if not pyproject.is_file():
            continue
        content = pyproject.read_text(encoding="utf-8")
        section = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", content)
        if section:
            name = re.search(
                r"(?m)^\s*name\s*=\s*(['\"])([^'\"]+)\1\s*(?:#.*)?$",
                section.group(1),
            )
            if name:
                return name.group(2)

    # Wheels do not include pyproject.toml. On Python 3.9, find the installed
    # distribution by matching its file list to this package instead of relying
    # on packages_distributions(), which was added in Python 3.10.
    package_marker = "container_docker_shim/__init__.py"
    for distribution in distributions():
        files = distribution.files or ()
        if any(str(file).replace("\\", "/").endswith(package_marker) for file in files):
            return distribution.metadata.get("Name")
    return None


def shim_version() -> str:
    """Return the installed shim version, or ``0+source`` when metadata is absent."""
    name = _distribution_name()
    if not name:
        return "0+source"
    try:
        return _pkg_version(name)
    except PackageNotFoundError:
        return "0+source"


__version__ = shim_version()
