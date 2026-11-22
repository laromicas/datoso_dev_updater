"""Shared fixtures for datoso-dev tests."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

PackageMaker = Callable[..., Path]


def write_package(  # noqa: PLR0913
    root: Path,
    name: str,
    version: str = '1.0.0',
    deps: Iterable[str] = (),
    *,
    skip: bool = False,
    pypi: bool = True,
    publish_workflow: bool = False,
    dist_name: str | None = None,
    static_version: bool = False,
    optional: dict[str, list[str]] | None = None,
) -> Path:
    """Create a minimal datoso-style package (src layout) under ``root/name``."""
    pkg = root / name
    (pkg / 'src' / name).mkdir(parents=True, exist_ok=True)
    (pkg / 'src' / name / '__init__.py').write_text(f'"""{name}."""\n__version__ = \'{version}\'\n')
    deps_toml = ', '.join(f'"{d}"' for d in deps)
    lines = [
        '[project]',
        f'name = "{dist_name or name}"',
        f'dependencies = [{deps_toml}]',
    ]
    if static_version:
        lines.append(f'version = "{version}"')
    else:
        lines.append('dynamic = ["version"]')
    if optional:
        lines.append('[project.optional-dependencies]')
        for extra, reqs in optional.items():
            lines.append(f'{extra} = [{", ".join(f"{chr(34)}{r}{chr(34)}" for r in reqs)}]')
    if not static_version:
        lines += ['[tool.setuptools.dynamic]', f'version = {{attr = "{name}.__version__"}}']
    tool = [*(['skip = true'] if skip else []), *([] if pypi else ['pypi = false'])]
    if tool:
        lines += ['[tool.datoso-dev]', *tool]
    if publish_workflow:
        (pkg / '.github' / 'workflows').mkdir(parents=True, exist_ok=True)
        (pkg / '.github' / 'workflows' / 'python-publish.yml').write_text(
            'on:\n  release:\n    types: [published]\njobs:\n  deploy:\n    steps:\n'
            '    - uses: pypa/gh-action-pypi-publish@27b31702a0e7fc50959f5ad993c78deac1bdfc29\n',
        )
    (pkg / 'pyproject.toml').write_text('\n'.join(lines) + '\n')
    return pkg


def write_gitmodules(root: Path, paths: Iterable[str]) -> None:
    """Write a .gitmodules file listing ``paths`` (mixed tab/space indentation, like the real one)."""
    blocks = []
    for i, path in enumerate(paths):
        indent = '\t' if i % 2 == 0 else '    '
        blocks.append(
            f'[submodule "{path}"]\n{indent}path = {path}\n{indent}url = git@github.com:laromicas/{path}.git\n',
        )
    (root / '.gitmodules').write_text(''.join(blocks))


@pytest.fixture
def make_package(tmp_path: Path) -> PackageMaker:
    """Return a factory that writes packages into ``tmp_path``."""

    def _make(name: str, *args: object, **kwargs: object) -> Path:
        return write_package(tmp_path, name, *args, **kwargs)

    return _make


@pytest.fixture
def sample_workspace(tmp_path: Path) -> Path:
    """A workspace shaped like the real datoso_dev (subset)."""
    write_package(
        tmp_path,
        'datoso',
        '1.1.1',
        ['requests>=2.31.0', 'rich>=14.0.0'],
        optional={'redump': ['datoso-seed-redump>=1.0.1']},
    )
    write_package(tmp_path, 'datoso_plugin_internetarchive', '1.1.1', ['datoso>=1.1.0', 'internetarchive>=4.1.0'])
    write_package(tmp_path, 'datoso_seed_redump', '1.0.1', ['datoso>=1.1.0'])
    write_package(tmp_path, 'datoso_seed_enhanced', '1.1.0', ['datoso>=1.1.0', 'datoso-plugin-internetarchive>=1.1.1'])
    write_package(tmp_path, 'datoso_seed_mameredump', '1.0.0', ['datoso>=1.1.1'], skip=True)
    write_package(tmp_path, 'datoso_seed_base', '1.0.0', ['datoso>=1.1.1'], pypi=False)
    (tmp_path / 'datoso_dev_updater').mkdir()
    (tmp_path / 'datoso_dev_updater' / 'pyproject.toml').write_text('[project]\nname = "datoso-dev"\n')
    write_gitmodules(
        tmp_path,
        [
            'datoso',
            'datoso_dev_updater',
            'datoso_plugin_internetarchive',
            'datoso_seed_redump',
            'datoso_seed_enhanced',
            'datoso_seed_mameredump',
            'datoso_seed_base',
            'datoso_seed_sfc_msu1',  # stale entry: listed but not checked out
        ],
    )
    return tmp_path


@pytest.fixture
def isolated_git(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run git with a throwaway HOME and identity so user config (signing, hooks...) never interferes."""
    home = tmp_path_factory.mktemp('home')
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(home / '.config'))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    for var in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'):
        monkeypatch.delenv(var, raising=False)
    for role in ('AUTHOR', 'COMMITTER'):
        monkeypatch.setenv(f'GIT_{role}_NAME', 'Test')
        monkeypatch.setenv(f'GIT_{role}_EMAIL', 'test@example.invalid')
    (home / '.gitconfig').write_text('[init]\n\tdefaultBranch = master\n[advice]\n\tdetachedHead = false\n')
