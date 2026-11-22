"""Tests for the interactive `ci` menu, driven by scripted answers (written first, TDD)."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from datoso_dev.ci import WORKFLOW_PATH, CiOutcome, CiResult, ci_targets, render_ci
from datoso_dev.ci_menu import Action, ci_menu, selection_options
from datoso_dev.workspace import discover

from .fakes import FakeGh, FakeGit, FakePrompter, FakeRepo


@pytest.fixture
def env(sample_workspace: Path):
    ws = discover(sample_workspace)
    git = FakeGit({pkg.path: FakeRepo() for pkg in ws.packages.values()})
    gh = FakeGh()
    out = StringIO()
    console = Console(file=out, width=200, force_terminal=False)
    return ws, git, gh, console, out


def run(env, answers: list[object]) -> tuple[int, FakePrompter]:
    ws, git, gh, console, _ = env
    prompter = FakePrompter(answers)
    code = ci_menu(ws, git, gh, prompter, console)
    return code, prompter


def labels(options) -> list[str]:
    return [o.label for o in options]


# --------------------------------------------------------------------------- selection_options


def test_only_actionable_packages_are_selectable(env):
    ws = env[0]
    pkgs = ws.packages
    results = [
        CiResult(pkgs['datoso_seed_redump'], CiOutcome.OPENED, 'workflow missing', dry_run=True),
        CiResult(
            pkgs['datoso_seed_enhanced'], CiOutcome.UPDATED, 'workflow outdated', 'https://x/pull/2', dry_run=True
        ),
        CiResult(pkgs['datoso_seed_base'], CiOutcome.UP_TO_DATE, dry_run=True),
        CiResult(pkgs['datoso_plugin_internetarchive'], CiOutcome.SKIPPED, 'uncommitted changes', dry_run=True),
    ]
    options = selection_options(results)
    assert [(o.value, o.disabled) for o in options] == [
        (pkgs['datoso_seed_redump'], None),
        (pkgs['datoso_seed_enhanced'], None),
        (pkgs['datoso_seed_base'], 'up to date'),
        (pkgs['datoso_plugin_internetarchive'], 'skipped: uncommitted changes'),
    ]
    assert 'will open PR' in options[0].label
    assert 'will update PR' in options[1].label


# --------------------------------------------------------------------------- menu loop


def test_quit_immediately_changes_nothing(env):
    code, prompter = run(env, [Action.QUIT])
    _, git, _gh, _, out = env
    assert code == 0
    assert git.commits == []
    assert 'datoso_seed_redump' in out.getvalue()  # status table shown before the menu
    assert [kind for kind, *_ in prompter.asked] == ['select']


def test_cancel_menu_is_quit(env):
    code, _ = run(env, [None])
    assert code == 0


def test_select_some_packages_and_confirm(env):
    ws, _git, gh, _, out = env
    redump, enhanced = ws.packages['datoso_seed_redump'], ws.packages['datoso_seed_enhanced']
    code, prompter = run(env, [Action.SYNC, [redump, enhanced], True, Action.QUIT])
    assert code == 0
    assert sorted(pr['repo'].name for pr in gh.created) == ['datoso_seed_enhanced', 'datoso_seed_redump']
    assert prompter.asked[2][1] == 'Open or update 2 PR(s)?'
    assert 'datoso_seed_redump: https://github.com/owner/datoso_seed_redump/pull/' in out.getvalue()


def test_declining_confirmation_does_nothing(env):
    ws, git, gh, _, _ = env
    code, _ = run(env, [Action.SYNC, [ws.packages['datoso_seed_redump']], False, Action.QUIT])
    assert code == 0
    assert gh.created == []
    assert git.commits == []


def test_empty_selection_goes_back_to_menu(env):
    _, _, gh, _, _ = env
    code, prompter = run(env, [Action.SYNC, [], Action.QUIT])
    assert code == 0
    assert gh.created == []
    assert [kind for kind, *_ in prompter.asked] == ['select', 'checkbox', 'select']


def test_menu_refreshes_after_sync(env):
    ws, git, _, _, _ = env
    redump = ws.packages['datoso_seed_redump']
    run(env, [Action.SYNC, [redump], True, Action.QUIT])
    # initial check of every target + the sync's own fetch of redump + a full re-check afterwards
    assert len(git.called('fetch')) == 2 * len(ci_targets(ws)) + 1


def test_nothing_actionable_skips_checkbox(env):
    ws, _, _, _, out = env
    for pkg in ws.packages.values():
        (pkg.path / WORKFLOW_PATH).parent.mkdir(parents=True, exist_ok=True)
        (pkg.path / WORKFLOW_PATH).write_text(render_ci(pkg))
    code, prompter = run(env, [Action.SYNC, Action.QUIT])
    assert code == 0
    assert 'Nothing to do' in out.getvalue()
    assert [kind for kind, *_ in prompter.asked] == ['select', 'select']


def test_show_diff(env):
    ws, git, _, _, out = env
    redump = ws.packages['datoso_seed_redump']
    (redump.path / WORKFLOW_PATH).parent.mkdir(parents=True)
    (redump.path / WORKFLOW_PATH).write_text('name: old\n')
    code, _prompter = run(env, [Action.DIFF, redump, Action.QUIT])
    assert code == 0
    text = out.getvalue()
    assert '-name: old' in text
    assert '+name: CI' in text
    assert git.commits == []
    assert len(git.called('fetch')) == len(ci_targets(ws))  # showing a diff doesn't re-check


def test_diff_only_offers_actionable_packages(env):
    ws = env[0]
    redump = ws.packages['datoso_seed_redump']
    (redump.path / WORKFLOW_PATH).parent.mkdir(parents=True)
    (redump.path / WORKFLOW_PATH).write_text(render_ci(redump))
    offered: list[str] = []

    def pick_back(options):
        offered.extend(labels(options))

    run(env, [Action.DIFF, pick_back, Action.QUIT])
    assert not any('datoso_seed_redump' in label for label in offered)
    assert any('datoso_seed_enhanced' in label for label in offered)


def test_menu_error_exit_code(env):
    ws, git, _, _, _ = env
    redump = ws.packages['datoso_seed_redump']
    git.repos[redump.path].fail_on = {'push'}
    code, _ = run(env, [Action.SYNC, [redump], True, Action.QUIT])
    assert code == 1
