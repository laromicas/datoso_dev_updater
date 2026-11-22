"""Format-preserving edits of ``pyproject.toml`` dependency floors and package versions (pure: text in, text out)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import tomlkit
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

_INIT_VERSION = re.compile(r"""^(__version__\s*=\s*)(['"])([^'"]*)(\2)""", re.MULTILINE)


@dataclass(frozen=True)
class FloorEdit:
    """One requirement string rewritten: ``old`` → ``new``."""

    dist: str  # the requirement's name as written
    old: str
    new: str


def _with_floor(req: Requirement, floor: Version) -> str:
    others = [str(spec) for spec in req.specifier if spec.operator != '>=']
    extras = f'[{",".join(sorted(req.extras))}]' if req.extras else ''
    marker = f'; {req.marker}' if req.marker else ''
    return f'{req.name}{extras}{",".join([f">={floor}", *others])}{marker}'


def set_floor(text: str, dist: str, floor: Version, *, extra: str | None = None) -> tuple[str, FloorEdit | None]:
    """Set the ``>=`` floor of ``dist`` in ``[project].dependencies`` (or in one optional ``extra``).

    Other specifiers, extras, markers, comments and layout are kept. Returns the text unchanged and ``None`` when
    ``dist`` isn't listed there or already has exactly this floor.
    """
    doc = tomlkit.parse(text)
    project = doc.get('project', {})
    array = project.get('dependencies') if extra is None else project.get('optional-dependencies', {}).get(extra)
    if array is None:
        return text, None
    wanted = canonicalize_name(dist)
    for index, item in enumerate(array):
        req = Requirement(str(item))
        if canonicalize_name(req.name) != wanted:
            continue
        new = _with_floor(req, floor)
        if new == str(item):
            return text, None
        array[index] = new
        return tomlkit.dumps(doc), FloorEdit(req.name, str(item), new)
    return text, None


def set_init_version(text: str, version: Version) -> str:
    """Rewrite the ``__version__ = '...'`` line, keeping its quote style."""
    new, count = _INIT_VERSION.subn(lambda m: f'{m[1]}{m[2]}{version}{m[4]}', text, count=1)
    if not count:
        msg = 'no __version__ assignment found'
        raise ValueError(msg)
    return new


def set_project_version(text: str, version: Version) -> str:
    """Rewrite a static ``[project].version``."""
    doc = tomlkit.parse(text)
    doc['project']['version'] = str(version)
    return tomlkit.dumps(doc)
