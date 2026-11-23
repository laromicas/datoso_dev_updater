"""Tests for the pure release planner (written first, TDD)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from packaging.version import Version

from datoso_dev.planner import PlanError, plan_release, released_version
from datoso_dev.state import PackageState
from datoso_dev.workspace import discover

V = Version
REDUMP = 'datoso_seed_redump'
PLUGIN = 'datoso_plugin_internetarchive'


@pytest.fixture
def ws(sample_workspace: Path):
    return discover(sample_workspace)


@pytest.fixture
def states(ws):
    """Every package clean, on master, in sync, released at its current version."""
    return {
        name: PackageState(
            pkg,
            branch='master',
            ahead=0,
            behind=0,
            last_tag=f'v{pkg.version}',
            last_tag_version=pkg.version,
            pypi_version=pkg.version if pkg.pypi else None,
        )
        for name, pkg in ws.packages.items()
    }


# --------------------------------------------------------------------------- released_version


def test_released_version_prefers_the_newest_of_tag_and_pypi(ws, states):
    state = replace(states['datoso'], last_tag_version=V('1.1.1'), pypi_version=V('1.1.2'))
    assert released_version(state) == V('1.1.2')
    assert released_version(replace(state, pypi_version=None)) == V('1.1.1')
    assert released_version(replace(state, pypi_version=None, last_tag_version=None)) is None


def test_released_version_ignores_pypi_for_github_only_packages(ws, states):
    state = replace(states['datoso_seed_base'], last_tag_version=V('1.0.0'), pypi_version=V('9.0.0'))
    assert released_version(state) == V('1.0.0')


# --------------------------------------------------------------------------- steps and policy


def test_single_seed_is_one_step_and_follows_core(ws, states):
    plan = plan_release(ws, states, {'datoso_seed_redump': V('1.0.2')})
    [[item]] = plan.steps
    assert (item.name, item.current, item.released, item.target) == (
        'datoso_seed_redump',
        V('1.0.1'),
        V('1.0.1'),
        V('1.0.2'),
    )
    assert [(c.dependency, c.target) for c in item.floor_changes] == [('datoso', V('1.1.1'))]
    assert (item.branch, item.tag, item.title) == ('release/v1.0.2', 'v1.0.2', 'Release datoso_seed_redump v1.0.2')
    assert item.blockers == []
    assert plan.ok


def test_core_plugin_and_seeds_are_ordered_in_steps(ws, states):
    selection = {
        'datoso': V('1.2.0'),
        PLUGIN: V('1.1.2'),
        'datoso_seed_redump': V('1.0.2'),
        'datoso_seed_enhanced': V('1.1.1'),
    }
    plan = plan_release(ws, states, selection)
    assert [[i.name for i in step] for step in plan.steps] == [
        ['datoso'],
        [PLUGIN, 'datoso_seed_redump'],
        ['datoso_seed_enhanced'],
    ]
    enhanced = plan.item('datoso_seed_enhanced')
    assert [(c.dependency, c.target) for c in enhanced.floor_changes] == [('datoso', V('1.2.0')), (PLUGIN, V('1.1.2'))]
    assert enhanced.step == 3


def test_extras_floors_are_planned_for_after_the_releases(ws, states):
    plan = plan_release(ws, states, {'datoso_seed_redump': V('1.0.2')})
    assert [(c.extra, c.dependency, c.target) for c in plan.extras] == [('redump', 'datoso_seed_redump', V('1.0.2'))]


def test_runnable_now_is_the_first_step_only(ws, states):
    plan = plan_release(ws, states, {'datoso': V('1.2.0'), 'datoso_seed_redump': V('1.0.2')})
    assert [i.name for i in plan.runnable_now] == ['datoso']
    assert [i.name for i in plan.waiting] == ['datoso_seed_redump']


def test_keep_an_already_bumped_version(ws, states):
    states['datoso_seed_redump'] = replace(
        states['datoso_seed_redump'], last_tag_version=V('1.0.0'), pypi_version=V('1.0.0')
    )
    plan = plan_release(ws, states, {'datoso_seed_redump': V('1.0.1')})  # __version__ is already 1.0.1
    assert plan.item('datoso_seed_redump').blockers == []


# --------------------------------------------------------------------------- blockers


@pytest.mark.parametrize(
    ('changes', 'blocker'),
    [
        ({'changed_files': ('a.py',)}, '1 uncommitted file(s)'),
        ({'branch': 'feature'}, 'on branch feature, not master'),
        ({'behind': 2}, '2 behind origin (pull first)'),
        ({'ahead': 1}, '1 unpushed commit(s)'),
        ({'ahead': None, 'behind': None}, 'no upstream'),
        ({'error': 'git failed'}, 'git failed'),
    ],
)
def test_repo_state_blocks(ws, states, changes: dict, blocker: str):
    states['datoso_seed_redump'] = replace(states['datoso_seed_redump'], **changes)
    plan = plan_release(ws, states, {'datoso_seed_redump': V('1.0.2')})
    assert plan.item('datoso_seed_redump').blockers == [blocker]
    assert not plan.ok


def test_version_not_newer_than_release_blocks(ws, states):
    plan = plan_release(ws, states, {'datoso_seed_redump': V('1.0.1')})
    assert plan.item('datoso_seed_redump').blockers == ['1.0.1 is not newer than the released 1.0.1']


def test_skipped_package_blocks_unless_included(ws, states):
    selection = {'datoso_seed_mameredump': V('1.0.1')}
    assert plan_release(ws, states, selection).item('datoso_seed_mameredump').blockers == [
        'skip = true (use --include-skipped)'
    ]
    assert plan_release(ws, states, selection, include_skipped=True).ok


def test_blocked_dependency_blocks_dependents_transitively(ws, states):
    states['datoso'] = replace(states['datoso'], changed_files=('x',))
    selection = {'datoso': V('1.2.0'), PLUGIN: V('1.1.2'), 'datoso_seed_enhanced': V('1.1.1')}
    plan = plan_release(ws, states, selection)
    assert plan.item(PLUGIN).blockers == ['depends on datoso, which is blocked']
    assert plan.item('datoso_seed_enhanced').blockers == [
        'depends on datoso, which is blocked',
        f'depends on {PLUGIN}, which is blocked',
    ]


def test_unknown_package_is_an_error(ws, states):
    with pytest.raises(PlanError, match='nope'):
        plan_release(ws, states, {'nope': V('1.0.0')})


def test_core_is_blocked_while_an_extra_points_at_an_unreleased_seed(ws, states):
    """datoso[redump] needs datoso-seed-redump>=1.0.1; if only 1.0.0 is out, `pip install datoso[redump]` breaks."""
    states[REDUMP] = replace(states[REDUMP], last_tag='v1.0.0', last_tag_version=V('1.0.0'), pypi_version=V('1.0.0'))
    plan = plan_release(ws, states, {'datoso': V('1.1.2')})
    assert plan.item('datoso').blockers == [
        'extra needs datoso-seed-redump>=1.0.1, not released yet (latest 1.0.0): release datoso_seed_redump first'
    ]


def test_template_is_refused(ws, states):
    with pytest.raises(PlanError, match='datoso_seed_base is a template'):
        plan_release(ws, states, {'datoso_seed_base': V('1.0.1')})


def test_empty_selection_is_an_error(ws, states):
    with pytest.raises(PlanError, match='nothing'):
        plan_release(ws, states, {})
