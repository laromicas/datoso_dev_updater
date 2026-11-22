"""Tests for workspace discovery and release-step grouping (written first, TDD)."""

from __future__ import annotations

from pathlib import Path

import pytest
from packaging.version import Version

from datoso_dev.workspace import (
    CycleError,
    Package,
    WorkspaceError,
    discover,
    find_workspace_root,
    parse_gitmodules,
    release_steps,
)

from .conftest import write_gitmodules, write_package

# --------------------------------------------------------------------------- parse_gitmodules


def test_parse_gitmodules_handles_tabs_spaces_and_order():
    text = (
        '[submodule "datoso"]\n\tpath = datoso\n\turl = git@x:a/datoso.git\n'
        '[submodule "datoso_docker"]\n    path = datoso_docker\n    url = git@x:a/datoso_docker.git\n'
        '# a comment\n'
        '[submodule "datoso_seed_tdc"]\n\tpath=datoso_seed_tdc\n'
    )
    assert parse_gitmodules(text) == ['datoso', 'datoso_docker', 'datoso_seed_tdc']


def test_parse_gitmodules_empty():
    assert parse_gitmodules('') == []


# --------------------------------------------------------------------------- discover


def test_discover_finds_only_datoso_packages_that_exist(sample_workspace: Path):
    ws = discover(sample_workspace)
    assert list(ws.packages) == [
        'datoso',
        'datoso_plugin_internetarchive',
        'datoso_seed_redump',
        'datoso_seed_enhanced',
        'datoso_seed_mameredump',
        'datoso_seed_base',
    ]  # .gitmodules order; tool repo and stale entry excluded


def test_discover_keeps_all_existing_submodule_paths_for_sync(sample_workspace: Path):
    ws = discover(sample_workspace)
    assert ws.submodule_paths == [
        sample_workspace / p
        for p in (
            'datoso',
            'datoso_dev_updater',
            'datoso_plugin_internetarchive',
            'datoso_seed_redump',
            'datoso_seed_enhanced',
            'datoso_seed_mameredump',
            'datoso_seed_base',
        )
    ]


def test_package_metadata(sample_workspace: Path):
    pkg = discover(sample_workspace).packages['datoso_seed_enhanced']
    assert isinstance(pkg, Package)
    assert pkg.path == sample_workspace / 'datoso_seed_enhanced'
    assert pkg.dist_name == 'datoso-seed-enhanced'
    assert pkg.version == Version('1.1.0')
    assert (
        pkg.version_file == sample_workspace / 'datoso_seed_enhanced' / 'src' / 'datoso_seed_enhanced' / '__init__.py'
    )
    assert pkg.skip is False


def test_skip_flag(sample_workspace: Path):
    assert discover(sample_workspace).packages['datoso_seed_mameredump'].skip is True


def test_workspace_dependencies_only_include_workspace_packages(sample_workspace: Path):
    ws = discover(sample_workspace)
    assert ws.deps_of('datoso') == set()  # optional-dependencies (extras) are NOT dependencies
    assert ws.deps_of('datoso_seed_redump') == {'datoso'}
    assert ws.deps_of('datoso_seed_enhanced') == {'datoso', 'datoso_plugin_internetarchive'}


def test_dist_names_are_normalized_when_matching(tmp_path: Path):
    write_package(tmp_path, 'datoso', '1.0.0')
    write_package(tmp_path, 'datoso_plugin_x', '1.0.0', ['datoso>=1.0'], dist_name='Datoso.Plugin-X')
    write_package(tmp_path, 'datoso_seed_y', '1.0.0', ["DATOSO_plugin_x>=1.0 ; python_version>'3'"])
    write_gitmodules(tmp_path, ['datoso', 'datoso_plugin_x', 'datoso_seed_y'])
    ws = discover(tmp_path)
    assert ws.packages['datoso_plugin_x'].dist_name == 'datoso-plugin-x'
    assert ws.deps_of('datoso_seed_y') == {'datoso_plugin_x'}


def test_static_version_is_supported(tmp_path: Path):
    write_package(tmp_path, 'datoso', '2.0.0', static_version=True)
    write_gitmodules(tmp_path, ['datoso'])
    assert discover(tmp_path).packages['datoso'].version == Version('2.0.0')


def test_missing_version_raises_with_file_name(tmp_path: Path):
    pkg = write_package(tmp_path, 'datoso', '1.0.0')
    (pkg / 'src' / 'datoso' / '__init__.py').write_text('"""no version here."""\n')
    write_gitmodules(tmp_path, ['datoso'])
    with pytest.raises(WorkspaceError, match=r'__init__\.py'):
        discover(tmp_path)


def test_discover_without_gitmodules_raises(tmp_path: Path):
    with pytest.raises(WorkspaceError, match=r'\.gitmodules'):
        discover(tmp_path)


# --------------------------------------------------------------------------- find_workspace_root


def test_find_workspace_root_walks_up(sample_workspace: Path):
    nested = sample_workspace / 'datoso_seed_redump' / 'src' / 'datoso_seed_redump'
    assert find_workspace_root(nested) == sample_workspace


def test_find_workspace_root_requires_datoso_submodule(tmp_path: Path):
    write_gitmodules(tmp_path, ['something_else'])
    with pytest.raises(WorkspaceError):
        find_workspace_root(tmp_path)


# --------------------------------------------------------------------------- release_steps


@pytest.fixture
def graph(tmp_path: Path):
    """Real-world shaped graph incl. a seed that depends on other seeds."""
    write_package(tmp_path, 'datoso', '1.1.1')
    write_package(tmp_path, 'datoso_plugin_internetarchive', '1.1.1', ['datoso>=1.1.0'])
    write_package(tmp_path, 'datoso_seed_redump', '1.0.1', ['datoso>=1.1.0'])
    write_package(tmp_path, 'datoso_seed_nointro', '1.1.1', ['datoso>=1.1.1', 'selenium==4.8.3'])
    write_package(tmp_path, 'datoso_seed_tdc', '1.1.0', ['datoso>=1.1.0'])
    write_package(tmp_path, 'datoso_seed_enhanced', '1.1.0', ['datoso>=1.1.0', 'datoso-plugin-internetarchive>=1.1.1'])
    write_package(
        tmp_path,
        'datoso_seed_private',
        '1.1.1',
        [
            'datoso>=1.1.1',
            'datoso-plugin-internetarchive>=1.1.1',
            'datoso-seed-nointro>=1.1.1',
            'datoso-seed-redump>=1.0.1',
        ],
    )
    write_gitmodules(
        tmp_path,
        [
            'datoso',
            'datoso_plugin_internetarchive',
            'datoso_seed_redump',
            'datoso_seed_nointro',
            'datoso_seed_tdc',
            'datoso_seed_enhanced',
            'datoso_seed_private',
        ],
    )
    return discover(tmp_path)


def test_steps_single_package(graph):
    assert release_steps(graph, ['datoso']) == [['datoso']]


def test_steps_empty_selection(graph):
    assert release_steps(graph, []) == []


def test_steps_full_dependency_order(graph):
    assert release_steps(graph, graph.packages) == [
        ['datoso'],
        ['datoso_plugin_internetarchive', 'datoso_seed_nointro', 'datoso_seed_redump', 'datoso_seed_tdc'],
        ['datoso_seed_enhanced', 'datoso_seed_private'],  # both depend only on step-2 packages
    ]


def test_steps_seeds_only_are_one_parallel_step(graph):
    assert release_steps(graph, ['datoso_seed_tdc', 'datoso_seed_redump']) == [
        ['datoso_seed_redump', 'datoso_seed_tdc'],
    ]


def test_unselected_dependencies_do_not_create_steps(graph):
    # IA plugin not in the batch: enhanced depends only on already-released packages -> first step
    assert release_steps(graph, ['datoso_seed_enhanced', 'datoso']) == [['datoso'], ['datoso_seed_enhanced']]
    assert release_steps(graph, ['datoso_seed_enhanced']) == [['datoso_seed_enhanced']]


def test_seed_depending_on_seed_goes_later(graph):
    assert release_steps(graph, ['datoso_seed_private', 'datoso_seed_nointro']) == [
        ['datoso_seed_nointro'],
        ['datoso_seed_private'],
    ]


def test_unknown_package_raises(graph):
    with pytest.raises(WorkspaceError, match='datoso_seed_nope'):
        release_steps(graph, ['datoso_seed_nope'])


def test_cycle_raises_and_names_members(tmp_path: Path):
    write_package(tmp_path, 'datoso', '1.0.0')
    write_package(tmp_path, 'datoso_seed_a', '1.0.0', ['datoso-seed-b>=1'])
    write_package(tmp_path, 'datoso_seed_b', '1.0.0', ['datoso-seed-a>=1'])
    write_gitmodules(tmp_path, ['datoso', 'datoso_seed_a', 'datoso_seed_b'])
    ws = discover(tmp_path)
    with pytest.raises(CycleError, match=r'datoso_seed_a.*datoso_seed_b'):
        release_steps(ws, ['datoso', 'datoso_seed_a', 'datoso_seed_b'])


# --------------------------------------------------------------------------- malformed packages


@pytest.mark.parametrize(
    ('pyproject', 'match'),
    [
        ('[project\nname = "datoso"', 'invalid pyproject'),
        ('[tool.x]\na = 1\n', 'invalid pyproject'),  # no [project]
        ('[project]\nname = "datoso"\ndynamic = ["version"]\n', 'no static version'),
        ('[project]\nname = "datoso"\nversion = "not-a-version"\n', 'invalid version'),
        ('[project]\nname = "datoso"\nversion = "1.0"\ndependencies = ["not a valid req !!"]\n', 'pyproject.toml'),
        (
            (
                '[project]\nname = "datoso"\ndynamic = ["version"]\n'
                '[tool.setuptools.dynamic]\nversion = {attr = "missing_mod.__version__"}\n'
            ),
            'version module not found',
        ),
    ],
)
def test_malformed_package_errors(tmp_path: Path, pyproject: str, match: str):
    (tmp_path / 'datoso').mkdir()
    (tmp_path / 'datoso' / 'pyproject.toml').write_text(pyproject)
    write_gitmodules(tmp_path, ['datoso'])
    with pytest.raises(WorkspaceError, match=match):
        discover(tmp_path)


# --------------------------------------------------------------------------- pypi flag & publish workflow


def test_pypi_flag_defaults_true_and_can_be_disabled(sample_workspace: Path):
    ws = discover(sample_workspace)
    assert ws.packages['datoso_seed_redump'].pypi is True
    assert ws.packages['datoso_seed_base'].pypi is False
    assert ws.packages['datoso_seed_base'].skip is False  # released on GitHub, just not on PyPI


def test_publish_workflow_detection(tmp_path: Path):
    write_package(tmp_path, 'datoso', '1.0.0', publish_workflow=True)
    write_package(tmp_path, 'datoso_seed_a', '1.0.0')
    write_gitmodules(tmp_path, ['datoso', 'datoso_seed_a'])
    ws = discover(tmp_path)
    assert ws.packages['datoso'].publishes_to_pypi is True
    assert ws.packages['datoso_seed_a'].publishes_to_pypi is False
