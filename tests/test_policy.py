"""Tests for the dependency policy: released packages follow the latest core and IA plugin (written first, TDD)."""

from __future__ import annotations

from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

from datoso_dev.policy import FloorChange, current_floor, extras_floor_changes, policy_changes, violations
from datoso_dev.workspace import discover

from .conftest import write_gitmodules, write_package

V = Version
PLUGIN = 'datoso_plugin_internetarchive'


@pytest.fixture
def ws(sample_workspace: Path):
    return discover(sample_workspace)


RELEASED = {
    'datoso': V('1.1.1'),
    PLUGIN: V('1.1.1'),
    'datoso_seed_redump': V('1.0.1'),
    'datoso_seed_enhanced': V('1.1.0'),
    'datoso_seed_base': V('1.0.0'),
}


def test_seed_alone_follows_latest_released_core(ws):
    """Core isn't in the batch: the seed's floor goes to the latest *released* core."""
    changes = policy_changes(ws, {'datoso_seed_redump': V('1.0.2')}, RELEASED)
    assert changes == {
        'datoso_seed_redump': [FloorChange('datoso_seed_redump', 'datoso', 'datoso', V('1.1.0'), V('1.1.1'))],
    }


def test_seed_follows_core_released_in_the_same_batch(ws):
    batch = {'datoso': V('1.2.0'), 'datoso_seed_redump': V('1.0.2')}
    changes = policy_changes(ws, batch, RELEASED)
    assert changes['datoso_seed_redump'][0].target == V('1.2.0')
    assert changes['datoso'] == []  # core has no workspace floors to follow


def test_follows_both_core_and_plugin(ws):
    batch = {'datoso': V('1.2.0'), PLUGIN: V('1.2.0'), 'datoso_seed_enhanced': V('1.1.1')}
    changes = policy_changes(ws, batch, RELEASED)
    assert [(c.dependency, c.current, c.target) for c in changes['datoso_seed_enhanced']] == [
        ('datoso', V('1.1.0'), V('1.2.0')),
        (PLUGIN, V('1.1.1'), V('1.2.0')),
    ]
    assert [(c.dependency, c.target) for c in changes[PLUGIN]] == [('datoso', V('1.2.0'))]


def test_floor_already_current_needs_no_change(ws):
    changes = policy_changes(ws, {'datoso_seed_base': V('1.0.1')}, RELEASED)  # base already has datoso>=1.1.1
    assert changes == {'datoso_seed_base': []}


def test_never_lowers_a_floor(tmp_path: Path):
    write_package(tmp_path, 'datoso', '1.1.1')
    write_package(tmp_path, 'datoso_seed_x', '1.0.0', ['datoso>=1.2.0'])
    write_gitmodules(tmp_path, ['datoso', 'datoso_seed_x'])
    ws = discover(tmp_path)
    assert policy_changes(ws, {'datoso_seed_x': V('1.0.1')}, {'datoso': V('1.1.1')}) == {'datoso_seed_x': []}


def test_unreleased_dependency_is_not_a_target(ws):
    released = {**RELEASED, 'datoso': None}  # core never released and not in the batch
    assert policy_changes(ws, {'datoso_seed_redump': V('1.0.2')}, released) == {'datoso_seed_redump': []}


def test_packages_outside_the_batch_are_untouched(ws):
    changes = policy_changes(ws, {'datoso': V('1.2.0')}, RELEASED)
    assert set(changes) == {'datoso'}


def test_seed_to_seed_dependencies_are_not_followed(tmp_path: Path):
    """Only core and the IA plugin are followed (private → nointro keeps its own floor)."""
    write_package(tmp_path, 'datoso', '1.1.1')
    write_package(tmp_path, 'datoso_seed_nointro', '1.1.1', ['datoso>=1.1.1'])
    write_package(tmp_path, 'datoso_seed_private', '1.0.0', ['datoso>=1.1.1', 'datoso-seed-nointro>=1.0'])
    write_gitmodules(tmp_path, ['datoso', 'datoso_seed_nointro', 'datoso_seed_private'])
    ws = discover(tmp_path)
    batch = {'datoso_seed_nointro': V('1.2.0'), 'datoso_seed_private': V('1.0.1')}
    assert policy_changes(ws, batch, {'datoso': V('1.1.1')})['datoso_seed_private'] == []


# --------------------------------------------------------------------------- current_floor / violations


@pytest.mark.parametrize(
    ('spec', 'expected'),
    [('datoso>=1.1.0', V('1.1.0')), ('datoso', None), ('datoso>=1.0,<2', V('1.0')), ('datoso>1.0', None)],
)
def test_current_floor(spec: str, expected: Version | None):
    assert current_floor(Requirement(spec)) == expected


def test_violations_cover_all_non_skipped_packages(ws):
    found = violations(ws, RELEASED)
    assert set(found) == {PLUGIN, 'datoso_seed_redump', 'datoso_seed_enhanced'}  # base is current; mameredump skipped
    assert all(change.target == V('1.1.1') for changes in found.values() for change in changes)


def test_violations_include_skipped(ws):
    assert 'datoso_seed_mameredump' not in violations(ws, RELEASED, include_skipped=True)  # it is current anyway
    released = {**RELEASED, 'datoso': V('1.2.0')}
    assert 'datoso_seed_mameredump' in violations(ws, released, include_skipped=True)


# --------------------------------------------------------------------------- core extras floors


def test_extras_floors_follow_released_seeds(tmp_path: Path):
    write_package(
        tmp_path,
        'datoso',
        '1.1.1',
        optional={
            'redump': ['datoso-seed-redump>=1.0.1'],
            'all': ['datoso-seed-redump>=1.0.1', 'datoso-seed-tdc>=1.1.0'],
        },
    )
    write_package(tmp_path, 'datoso_seed_redump', '1.0.1', ['datoso>=1.1.1'])
    write_package(tmp_path, 'datoso_seed_tdc', '1.1.0', ['datoso>=1.1.1'])
    write_gitmodules(tmp_path, ['datoso', 'datoso_seed_redump', 'datoso_seed_tdc'])
    ws = discover(tmp_path)
    changes = extras_floor_changes(ws, {'datoso_seed_redump': V('1.0.2'), 'datoso_seed_tdc': V('1.1.0')})
    assert [(c.extra, c.dependency, c.current, c.target) for c in changes] == [
        ('redump', 'datoso_seed_redump', V('1.0.1'), V('1.0.2')),
        ('all', 'datoso_seed_redump', V('1.0.1'), V('1.0.2')),
    ]  # tdc's floor is already 1.1.0
    assert all(c.package == 'datoso' for c in changes)


def test_extras_floors_ignore_core_itself(ws):
    assert extras_floor_changes(ws, {'datoso': V('1.2.0')}) == []
