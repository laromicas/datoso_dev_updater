"""Tests for `datoso-dev prs`: listing open PRs and merging only your own."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from datoso_dev import cli
from datoso_dev.ports import Checks, PrInfo
from datoso_dev.prs import OpenPr, blocker, list_open, merge
from datoso_dev.workspace import discover

from .fakes import FakeGh, FakeGit, FakePrompter, FakeRepo

runner = CliRunner()


def info(number: int, author: str = 'me', checks: Checks = Checks.PASSED, **kwargs: object) -> PrInfo:
    return PrInfo(number, f'PR {number}', f'feat/{number}', f'https://github.com/o/r/pull/{number}', author,
                  checks=checks, **kwargs)  # fmt: skip


# --------------------------------------------------------------------------- module


@pytest.mark.parametrize(
    ('pr', 'reason'),
    [
        (info(1), ''),
        (info(1, author='someone'), 'opened by someone, not you'),
        (info(1, author=''), 'opened by unknown, not you'),
        (info(1, draft=True), 'draft'),
        (info(1, mergeable='CONFLICTING'), 'conflicts with the base branch'),
    ],
)
def test_blocker(pr, reason):
    assert blocker(pr, 'me') == reason


def test_green_needs_passing_or_no_checks(tmp_path: Path):
    assert OpenPr(tmp_path, info(1)).green
    assert OpenPr(tmp_path, info(1, checks=Checks.NONE)).green
    assert not OpenPr(tmp_path, info(1, checks=Checks.FAILED)).green
    assert not OpenPr(tmp_path, info(1), blocker='draft').green


def test_list_open_keeps_order_and_reports_failures(tmp_path: Path):
    a, b, c = tmp_path / 'a', tmp_path / 'b', tmp_path / 'c'
    gh = FakeGh()
    gh.listed = {a: [info(1), info(2, author='bot')], c: [info(3)]}
    gh.fail_list = {b}
    prs, errors = list_open([a, b, c], gh, 'me')
    assert [(item.repo, item.pr.number, item.blocker) for item in prs] == [
        (a, 1, ''), (a, 2, 'opened by bot, not you'), (c, 3, ''),
    ]  # fmt: skip
    assert errors == [f'b: gh pr list failed in {b}']


def test_merge_rechecks_each_pr_on_github(tmp_path: Path):
    gh = FakeGh()
    mine, stolen, gone = info(1), info(2), info(3)
    gh.listed = {tmp_path: [mine, info(2, author='someone')]}  # #2 changed hands, #3 was closed meanwhile
    results = merge([OpenPr(tmp_path, pr) for pr in (mine, stolen, gone)], gh, 'me')
    assert gh.merged == [mine.url]
    assert [(r.merged, r.blocker) for r in results] == [
        (True, ''), (False, 'opened by someone, not you'), (False, 'no longer open'),
    ]  # fmt: skip


def test_merge_failure_is_reported(tmp_path: Path):
    gh = FakeGh()
    gh.listed = {tmp_path: [info(1)]}
    gh.fail_merge = True
    (result,) = merge([OpenPr(tmp_path, info(1))], gh, 'me')
    assert not result.merged
    assert 'base branch policy' in result.error


# --------------------------------------------------------------------------- CLI


@pytest.fixture
def fakes(sample_workspace: Path, monkeypatch: pytest.MonkeyPatch):
    ws = discover(sample_workspace)
    core, redump = ws.packages['datoso'].path, ws.packages['datoso_seed_redump'].path
    git = FakeGit({path: FakeRepo(branch='feat/1', branches={'master', 'feat/1'}) for path in ws.submodule_paths})
    gh = FakeGh()
    gh.listed = {
        core: [info(16)],
        redump: [info(3, checks=Checks.FAILED), info(4, author='dependabot')],
    }
    monkeypatch.setattr(cli, 'make_git', lambda: git)
    monkeypatch.setattr(cli, 'make_gh', lambda: gh)
    monkeypatch.setattr(cli, 'is_interactive', lambda: False)
    monkeypatch.delenv(cli.WORKSPACE_ENV, raising=False)
    return ws, git, gh


def invoke(ws, *args: str, **kwargs):
    return runner.invoke(cli.app, ['prs', '-w', str(ws.root), *args], **kwargs)


def test_list_only(fakes):
    ws, _, gh = fakes
    result = invoke(ws)
    assert result.exit_code == 0, result.output
    assert 'you: me' in result.output
    assert 'dependabot' in result.output
    assert gh.merged == []


def test_yes_merges_green_prs_of_yours_and_syncs(fakes):
    ws, git, gh = fakes
    result = invoke(ws, '--merge', '--yes')
    assert result.exit_code == 0, result.output
    assert gh.merged == ['https://github.com/o/r/pull/16']
    assert git.repos[ws.packages['datoso'].path].branch == 'master'
    assert git.repos[ws.packages['datoso_seed_redump'].path].branch == 'feat/1'


def test_include_failing_merges_red_ones_too(fakes):
    ws, _, gh = fakes
    result = invoke(ws, '--merge', '--yes', '--include-failing')
    assert result.exit_code == 0, result.output
    assert len(gh.merged) == 2  # never dependabot's


def test_checkbox_offers_only_yours(fakes, monkeypatch: pytest.MonkeyPatch):
    ws, _, gh = fakes
    monkeypatch.setattr(cli, 'is_interactive', lambda: True)

    def pick(options):
        by_number = {option.value.pr.number: option for option in options}
        assert by_number[4].disabled == 'opened by dependabot, not you'
        assert by_number[16].checked
        assert not by_number[3].checked
        return [by_number[3].value]

    prompter = FakePrompter([pick])
    monkeypatch.setattr(cli, 'make_prompter', lambda: prompter)
    result = invoke(ws, '--merge', input='y\n')
    assert result.exit_code == 0, result.output
    assert gh.merged == ['https://github.com/o/r/pull/3']


def test_nothing_picked(fakes, monkeypatch: pytest.MonkeyPatch):
    ws, _, gh = fakes
    monkeypatch.setattr(cli, 'is_interactive', lambda: True)
    monkeypatch.setattr(cli, 'make_prompter', lambda: FakePrompter([[]]))
    result = invoke(ws, '--merge')
    assert result.exit_code == 0
    assert 'Nothing to merge' in result.output
    assert gh.merged == []


def test_merge_without_terminal_needs_yes(fakes):
    ws, _, gh = fakes
    result = invoke(ws, '--merge')
    assert result.exit_code == 2
    assert gh.merged == []


def test_failures_exit_non_zero(fakes):
    ws, _, gh = fakes
    gh.fail_list = {ws.packages['datoso_seed_redump'].path}
    gh.fail_merge = True
    result = invoke(ws, '--merge', '--yes')
    assert result.exit_code == 1
    assert 'Could not list' in result.output


def test_no_open_prs(fakes):
    ws, _, gh = fakes
    gh.listed = {}
    result = invoke(ws, 'datoso_dev_updater')
    assert result.exit_code == 0
    assert 'No open pull requests' in result.output


def test_unknown_repository(fakes):
    ws, _, _ = fakes
    assert invoke(ws, 'nope').exit_code == 2


def test_current_user_failure(fakes, monkeypatch: pytest.MonkeyPatch):
    ws, _, gh = fakes

    def broken(_repo):
        msg = 'not logged in'
        raise cli.GhError(msg)

    monkeypatch.setattr(gh, 'current_user', broken)
    result = invoke(ws)
    assert result.exit_code == 1
    assert 'not logged in' in result.output
