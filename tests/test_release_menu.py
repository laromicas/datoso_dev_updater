"""Tests for the interactive release wizard (selection and versions), with scripted answers (written first, TDD)."""

from __future__ import annotations

from dataclasses import replace
from io import StringIO
from pathlib import Path

import pytest
from packaging.version import Version
from rich.console import Console

from datoso_dev.release_menu import choose_release
from datoso_dev.state import PackageState
from datoso_dev.versioning import Part
from datoso_dev.workspace import discover

from .fakes import FakePrompter

V = Version
REDUMP = 'datoso_seed_redump'


@pytest.fixture
def ws(sample_workspace: Path):
    return discover(sample_workspace)


@pytest.fixture
def states(ws):
    states = {
        name: PackageState(
            pkg,
            branch='master',
            ahead=0,
            behind=0,
            last_tag=f'v{pkg.version}',
            last_tag_version=pkg.version,
            pypi_version=pkg.version,
        )
        for name, pkg in ws.packages.items()
    }
    states[REDUMP] = replace(states[REDUMP], commits_since_tag=3)  # needs a release
    return states


def choose(ws, states, answers, **kwargs):
    prompter = FakePrompter(answers)
    console = Console(file=StringIO(), width=200)
    return choose_release(ws, states, prompter, console, **kwargs), prompter


def by_label(prefix: str):
    """Answer a select prompt with the option whose label starts with ``prefix``."""
    return lambda options: next(o.value for o in options if o.label.startswith(prefix))


def test_needs_release_is_prechecked_and_skipped_hidden(ws, states):
    checkbox_options = []

    def pick(options):
        checkbox_options.extend(options)

    choose(ws, states, [pick])
    assert {o.value: o.checked for o in checkbox_options} == {
        'datoso': False,
        'datoso_plugin_internetarchive': False,
        REDUMP: True,
        'datoso_seed_enhanced': False,
        'datoso_seed_base': False,
    }  # mameredump (skip = true) isn't offered


def test_include_skipped_offers_them(ws, states):
    seen = []
    choose(ws, states, [lambda options: seen.extend(o.value for o in options)], include_skipped=True)
    assert 'datoso_seed_mameredump' in seen


def test_pick_packages_and_versions(ws, states):
    selection, prompter = choose(ws, states, [[REDUMP, 'datoso'], by_label('minor'), by_label('patch')])
    assert selection == {REDUMP: V('1.1.0'), 'datoso': V('1.1.2')}
    assert [message for kind, message, _ in prompter.asked if kind == 'select'] == [
        'datoso_seed_redump 1.0.1: release as',
        'datoso 1.1.1: release as',
    ]


def test_bump_given_skips_version_questions(ws, states):
    selection, prompter = choose(ws, states, [[REDUMP]], part=Part.PATCH)
    assert selection == {REDUMP: V('1.0.2')}
    assert [kind for kind, *_ in prompter.asked] == ['checkbox']


def test_pre_release_label(ws, states):
    selection, _ = choose(ws, states, [[REDUMP]], part=Part.MINOR, pre='rc')
    assert selection == {REDUMP: V('1.1.0rc1')}


def test_only_prechecks_those(ws, states):
    seen = {}
    choose(ws, states, [lambda options: seen.update({o.value: o.checked for o in options})], only=['datoso'])
    assert seen['datoso'] is True
    assert seen[REDUMP] is False  # --only replaces the default pre-selection


def test_keep_is_offered_for_an_already_bumped_version(ws, states):
    states[REDUMP] = replace(states[REDUMP], last_tag='v1.0.0', last_tag_version=V('1.0.0'), pypi_version=V('1.0.0'))
    labels = []

    def pick_keep(options):
        labels.extend(o.label for o in options)
        return options[0].value

    selection, _ = choose(ws, states, [[REDUMP], pick_keep])
    assert labels[0] == 'keep 1.0.1 (bumped, not released yet)'
    assert selection == {REDUMP: V('1.0.1')}


@pytest.mark.parametrize('answers', [[None], [[]], [[REDUMP], None]])
def test_escape_or_empty_selection_cancels(ws, states, answers):
    selection, _ = choose(ws, states, answers)
    assert selection is None


def test_package_with_error_is_disabled(ws, states):
    states[REDUMP] = replace(states[REDUMP], error='git failed')
    seen = {}
    choose(ws, states, [lambda options: seen.update({o.value: o.disabled for o in options})])
    assert seen[REDUMP] == 'git failed'
