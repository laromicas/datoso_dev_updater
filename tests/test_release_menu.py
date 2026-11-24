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
    }  # mameredump (skip = true) and base (template) aren't offered


def test_ready_to_release_is_prechecked_too(ws, states):
    states['datoso_seed_enhanced'] = replace(
        states['datoso_seed_enhanced'], last_tag='v1.0.0', last_tag_version=V('1.0.0'), pypi_version=V('1.0.0')
    )  # version already bumped past the release
    seen = {}
    choose(ws, states, [lambda options: seen.update({o.value: o.checked for o in options})])
    assert seen['datoso_seed_enhanced'] is True


def test_include_skipped_offers_them(ws, states):
    seen = []
    choose(ws, states, [lambda options: seen.extend(o.value for o in options)], include_skipped=True)
    assert 'datoso_seed_mameredump' in seen
    assert 'datoso_seed_base' not in seen  # a template is never offered


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


@pytest.mark.parametrize('answers', [[None], [[]], [[REDUMP], None, None]])
def test_escape_or_empty_selection_cancels(ws, states, answers):
    selection, _ = choose(ws, states, answers)
    assert selection is None


def test_escape_on_a_version_goes_back_to_the_previous_question(ws, states):
    selection, prompter = choose(
        ws, states, [[REDUMP, 'datoso'], by_label('minor'), None, by_label('patch'), by_label('major')]
    )
    assert selection == {REDUMP: V('1.0.2'), 'datoso': V('2.0.0')}
    messages = [message for _, message, _ in prompter.asked]
    assert messages == [
        'Packages to release',
        'datoso_seed_redump 1.0.1: release as',
        'datoso 1.1.1: release as',  # Esc here...
        'datoso_seed_redump 1.0.1: release as',  # ...goes back one question
        'datoso 1.1.1: release as',
    ]
    assert prompter.defaults[2] == V('1.1.0')  # the earlier answer is pre-selected


def test_escape_on_the_first_version_goes_back_to_the_packages(ws, states):
    seen = []

    def repick(options):
        seen.extend(o.value for o in options if o.checked)
        return ['datoso']

    selection, _ = choose(ws, states, [[REDUMP], None, repick, by_label('patch')])
    assert seen == [REDUMP]  # earlier picks still ticked
    assert selection == {'datoso': V('1.1.2')}


def test_previous_answers_resume_at_the_last_question(ws, states):
    """Coming back from a later screen (Esc on "What should this run do?") reopens the last version question."""
    selection, prompter = choose(ws, states, [by_label('major')], previous={REDUMP: V('1.0.2'), 'datoso': V('1.1.2')})
    assert selection == {REDUMP: V('1.0.2'), 'datoso': V('2.0.0')}
    assert [message for _, message, _ in prompter.asked] == ['datoso 1.1.1: release as']
    assert prompter.defaults == [V('1.1.2')]


def test_package_with_error_is_disabled(ws, states):
    states[REDUMP] = replace(states[REDUMP], error='git failed')
    seen = {}
    choose(ws, states, [lambda options: seen.update({o.value: o.disabled for o in options})])
    assert seen[REDUMP] == 'git failed'


# --------------------------------------------------------------------------- custom version


def test_custom_version_typed_by_hand(ws, states):
    selection, prompter = choose(ws, states, [[REDUMP], by_label('custom'), '1.3.0'])
    assert selection == {REDUMP: V('1.3.0')}
    assert prompter.asked[-1][:2] == ('text', 'datoso_seed_redump: version to release')
    assert prompter.errors == [None]


@pytest.mark.parametrize(
    ('typed', 'error'),
    [
        ('banana', 'banana is not a valid version'),
        ('', '(empty) is not a valid version'),
        ('1.0.1', 'must be newer than the last release 1.0.1'),
        ('0.9', 'must be newer than the last release 1.0.1'),
    ],
)
def test_invalid_custom_version_goes_back_to_the_choices(ws, states, typed, error):
    selection, prompter = choose(ws, states, [[REDUMP], by_label('custom'), typed, by_label('patch')])
    assert selection == {REDUMP: V('1.0.2')}
    assert prompter.errors == [error]


def test_escape_on_custom_goes_back_to_the_choices(ws, states):
    selection, _ = choose(ws, states, [[REDUMP], by_label('custom'), None, by_label('minor')])
    assert selection == {REDUMP: V('1.1.0')}


def test_custom_version_is_not_lower_than_the_bumped_one(ws, states):
    from datoso_dev.versioning import check_custom  # noqa: PLC0415

    assert check_custom('1.2.0', V('1.3.0'), V('1.1.0')) == 'must not be lower than __version__ 1.3.0'
    assert check_custom(' 1.3.0rc1 ', V('1.3.0rc1'), None) is None


def test_previous_custom_answer_is_shown_and_offered(ws, states):
    seen = {}

    def pick_custom(options):
        seen['labels'] = [o.label for o in options]
        return 'custom'

    selection, prompter = choose(ws, states, [pick_custom, '1.4.0'], previous={REDUMP: V('1.3.0')})
    assert 'custom (1.3.0)' in seen['labels']
    assert prompter.defaults == ['custom', '1.3.0']  # select highlights it, the text starts from it
    assert selection == {REDUMP: V('1.4.0')}
