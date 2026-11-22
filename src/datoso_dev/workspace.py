"""Workspace discovery and dependency graph for the datoso multi-repo workspace.

Pure module: reads files, never runs git or touches the network.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

PACKAGE_PREFIXES = ('datoso_seed_', 'datoso_plugin_')
CORE = 'datoso'

_SUBMODULE_PATH = re.compile(r'^\s*path\s*=\s*(.+?)\s*$', re.MULTILINE)
VERSION_LINE = re.compile(r"""^__version__\s*=\s*['"]([^'"]+)['"]""", re.MULTILINE)


class WorkspaceError(Exception):
    """The workspace or one of its packages is malformed."""


class CycleError(WorkspaceError):
    """The dependency graph of the selected packages contains a cycle."""


@dataclass(frozen=True)
class Package:
    """A releasable package of the workspace (core, plugin or seed)."""

    name: str  # directory / import name, e.g. ``datoso_seed_redump``
    path: Path
    dist_name: str  # canonical PyPI name, e.g. ``datoso-seed-redump``
    version: Version
    version_file: Path | None  # file holding ``__version__`` (None for a static pyproject version)
    requirements: tuple[Requirement, ...] = ()
    skip: bool = False  # excluded from releases entirely
    pypi: bool = True  # False: released on GitHub only, never published to PyPI
    publishes_to_pypi: bool = False  # a GitHub workflow uploads to PyPI on release
    extras: tuple[tuple[str, tuple[Requirement, ...]], ...] = ()  # optional-dependencies, in file order


@dataclass
class Workspace:
    """All packages of a workspace, in ``.gitmodules`` order."""

    root: Path
    packages: dict[str, Package] = field(default_factory=dict)
    submodule_paths: list[Path] = field(default_factory=list)

    def by_dist_name(self) -> dict[str, str]:
        """Map canonical dist name -> package name."""
        return {pkg.dist_name: name for name, pkg in self.packages.items()}

    def deps_of(self, name: str) -> set[str]:
        """Workspace packages that ``name`` depends on (runtime ``dependencies`` only, not extras)."""
        index = self.by_dist_name()
        deps = {
            index[canonicalize_name(req.name)]
            for req in self.packages[name].requirements
            if canonicalize_name(req.name) in index
        }
        deps.discard(name)
        return deps


def parse_gitmodules(text: str) -> list[str]:
    """Return submodule paths from the contents of a ``.gitmodules`` file, in file order."""
    return [m.group(1) for m in _SUBMODULE_PATH.finditer(text)]


def is_package_name(name: str) -> bool:
    """Whether a directory name follows the datoso package naming convention."""
    return name == CORE or name.startswith(PACKAGE_PREFIXES)


def find_workspace_root(start: Path) -> Path:
    """Walk up from ``start`` to the directory whose ``.gitmodules`` lists the ``datoso`` core submodule."""
    for candidate in [start, *start.parents]:
        gitmodules = candidate / '.gitmodules'
        if gitmodules.is_file() and CORE in parse_gitmodules(gitmodules.read_text()):
            return candidate
    msg = f'No datoso workspace (.gitmodules with a "{CORE}" submodule) found at or above {start}'
    raise WorkspaceError(msg)


def discover(root: Path) -> Workspace:
    """Discover the packages listed in ``root/.gitmodules`` that exist on disk."""
    gitmodules = root / '.gitmodules'
    if not gitmodules.is_file():
        msg = f'{gitmodules} not found'
        raise WorkspaceError(msg)
    workspace = Workspace(root=root)
    for rel in parse_gitmodules(gitmodules.read_text()):
        path = root / rel
        if not path.is_dir():
            continue  # stale entry
        workspace.submodule_paths.append(path)
        if is_package_name(path.name) and (path / 'pyproject.toml').is_file():
            workspace.packages[path.name] = load_package(path)
    return workspace


def load_package(path: Path) -> Package:
    """Load one package's metadata from its ``pyproject.toml``."""
    pyproject_path = path / 'pyproject.toml'
    try:
        data = tomllib.loads(pyproject_path.read_text())
        project = data['project']
    except (tomllib.TOMLDecodeError, KeyError) as exc:
        msg = f'{pyproject_path}: invalid pyproject ({exc})'
        raise WorkspaceError(msg) from exc

    settings = data.get('tool', {}).get('datoso-dev', {})
    version, version_file = _read_version(path, data)
    try:
        requirements = tuple(Requirement(r) for r in project.get('dependencies', []))
        extras = tuple(
            (extra, tuple(Requirement(r) for r in reqs))
            for extra, reqs in project.get('optional-dependencies', {}).items()
        )
    except InvalidRequirement as exc:
        msg = f'{pyproject_path}: {exc}'
        raise WorkspaceError(msg) from exc

    return Package(
        name=path.name,
        path=path,
        dist_name=canonicalize_name(project['name']),
        version=version,
        version_file=version_file,
        requirements=requirements,
        extras=extras,
        skip=bool(settings.get('skip', False)),
        pypi=bool(settings.get('pypi', True)),
        publishes_to_pypi=has_publish_workflow(path),
    )


def has_publish_workflow(path: Path) -> bool:
    """Whether any GitHub workflow of the package uploads to PyPI."""
    workflows = path / '.github' / 'workflows'
    if not workflows.is_dir():
        return False
    markers = ('pypa/gh-action-pypi-publish', 'twine upload')
    # A guard like ``if: github.repository != 'owner/<package>'`` disables the upload in the package's own repo.
    own_repo_guard = re.compile(r'github\.repository\s*!=\s*[\'"][^/\'"]+/' + re.escape(path.name) + r'[\'"]')
    for wf in sorted(workflows.iterdir()):
        if wf.suffix not in {'.yml', '.yaml'}:
            continue
        text = wf.read_text(errors='ignore')
        if any(marker in text for marker in markers) and not own_repo_guard.search(text):
            return True
    return False


def _read_version(path: Path, data: dict) -> tuple[Version, Path | None]:
    project = data['project']
    if 'version' in project:
        return _parse_version(project['version'], path / 'pyproject.toml'), None
    attr = data.get('tool', {}).get('setuptools', {}).get('dynamic', {}).get('version', {}).get('attr')
    if not attr:
        msg = f'{path / "pyproject.toml"}: no static version and no [tool.setuptools.dynamic] version attr'
        raise WorkspaceError(msg)
    module = attr.rsplit('.', 1)[0].split('.')
    candidates = [path / 'src' / Path(*module) / '__init__.py', path / 'src' / Path(*module).with_suffix('.py')]
    version_file = next((c for c in candidates if c.is_file()), candidates[0])
    if not version_file.is_file():
        msg = f'{version_file}: version module not found'
        raise WorkspaceError(msg)
    match = VERSION_LINE.search(version_file.read_text())
    if not match:
        msg = f'{version_file}: no __version__ assignment found'
        raise WorkspaceError(msg)
    return _parse_version(match.group(1), version_file), version_file


def _parse_version(value: str, source: Path) -> Version:
    try:
        return Version(value)
    except InvalidVersion as exc:
        msg = f'{source}: invalid version {value!r}'
        raise WorkspaceError(msg) from exc


def release_steps(workspace: Workspace, selected: Iterable[str]) -> list[list[str]]:
    """Group ``selected`` packages into ordered release steps.

    A package goes in the step after the latest step of any *selected* package it depends on. Dependencies that
    are not selected are assumed to be already released and impose no ordering. Names within a step are sorted.
    """
    chosen = list(dict.fromkeys(selected))
    unknown = [name for name in chosen if name not in workspace.packages]
    if unknown:
        msg = f'Unknown package(s): {", ".join(unknown)}'
        raise WorkspaceError(msg)

    pending = {name: workspace.deps_of(name) & set(chosen) for name in chosen}
    steps: list[list[str]] = []
    while pending:
        ready = sorted(name for name, deps in pending.items() if not deps)
        if not ready:
            msg = f'Dependency cycle between: {", ".join(sorted(pending))}'
            raise CycleError(msg)
        steps.append(ready)
        for name in ready:
            del pending[name]
        for deps in pending.values():
            deps.difference_update(ready)
    return steps


def resolve_name(ws: Workspace, name: str) -> str:
    """Accept full or short package names: ``redump`` → ``datoso_seed_redump``, ``internetarchive`` → the plugin."""
    for candidate in (name, *(f'{prefix}{name}' for prefix in PACKAGE_PREFIXES)):
        if candidate in ws.packages:
            return candidate
    msg = f'unknown package: {name}'
    raise WorkspaceError(msg)
