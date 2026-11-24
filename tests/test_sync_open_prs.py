"""`datoso-dev sync` asks before switching repos whose branch has an open PR of yours (only yours)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from datoso_dev import cli
from datoso_dev.ports import GhError, PrInfo
from datoso_dev.prs import branch_prs
from datoso_dev.workspace import discover

from .fakes import FakeGh, FakeGit, FakePrompter, FakeRepo

runner = CliRunner()
BRANCH = 'feat/pointers-command'


def pr(number: int, branch: str = BRANCH, author: str = 'me') -> PrInfo:
    return PrInfo(number, f'PR {number}', branch, f'https://github.com/o/r/pull/{number}', author)


@pytest.fixture
def env(sample_workspace: Path, monkeypatch: pytest.MonkeyPatch):
    ws = discover(sample_workspace)
    tool, redump = ws.root / 'datoso_dev_updater', ws.packages['datoso_seed_redump'].path
    repos = {path: FakeRepo() for path in ws.submodule_paths}
    repos[tool] = FakeRepo(branch=BRANCH, branches={'master', BRANCH})
    repos[redump] = FakeRepo(branch='feat/other', branches={'master', 'feat/other'})
    git, gh = FakeGit(repos), FakeGh()
    gh.listed = {tool: [pr(10)], redump: [pr(4, 'feat/other', author='someone')]}  # redump's PR isn't yours
    monkeypatch.setattr(cli, 'make_git', lambda: git)
    monkeypatch.setattr(cli, 'make_gh', lambda: gh)
    monkeypatch.setattr(cli, 'is_interactive', lambda: False)
    monkeypatch.delenv(cli.WORKSPACE_ENV, raising=False)
    return ws, git, gh, tool, redump


def invoke(ws, *args: str, **kwargs):
    return runner.invoke(cli.app, ['sync', '-w', str(ws.root), *args], env={'COLUMNS': '200'}, **kwargs)


def test_branch_prs_only_yours_and_only_the_checked_out_branch(env, tmp_path):
    ws, git, gh, tool, _redump = env
    gh.listed[tool].append(pr(11, 'feat/elsewhere'))
    found = branch_prs(ws.submodule_paths, git, gh, 'me')
    assert {path: p.number for path, p in found.items()} == {tool: 10}


def test_branch_prs_ignores_errors(env):
    _ws, git, gh, tool, _ = env
    gh.fail_list = {tool}
    assert branch_prs([tool], git, gh, 'me') == {}


def test_without_a_terminal_they_stay_on_their_branch(env):
    ws, git, _, tool, redump = env
    result = invoke(ws)
    assert result.exit_code == 0, result.output
    assert 'your PR #10 is open on feat/pointers-command: kept on it' in result.output
    assert git.repos[tool].branch == BRANCH
    assert git.repos[redump].branch == 'master'  # someone else's PR: synced as before


def test_yes_switches_them_too(env):
    ws, git, _, tool, _ = env
    result = invoke(ws, '--yes')
    assert result.exit_code == 0, result.output
    assert 'on a branch with your open PR' in result.output
    assert git.repos[tool].branch == 'master'


@pytest.mark.parametrize(
    ('answer', 'branch', 'code'),
    [('keep', BRANCH, 0), ('switch', 'master', 0), ('cancel', BRANCH, 1), (None, BRANCH, 1)],
)
def test_asks_what_to_do(env, monkeypatch: pytest.MonkeyPatch, answer, branch, code):
    ws, git, _, tool, _ = env
    monkeypatch.setattr(cli, 'is_interactive', lambda: True)
    prompter = FakePrompter([answer])
    monkeypatch.setattr(cli, 'make_prompter', lambda: prompter)
    result = invoke(ws)
    assert result.exit_code == code, result.output
    assert git.repos[tool].branch == branch
    assert 'open PR of yours' in prompter.asked[0][1]
    assert prompter.defaults == ['keep']


def test_dry_run_only_annotates(env):
    ws, git, _, tool, _ = env
    result = invoke(ws, '--dry-run')
    assert result.exit_code == 0, result.output
    assert 'your PR #10 is open on feat/pointers-command' in result.output
    assert git.repos[tool].branch == BRANCH


def test_github_unavailable_warns_and_syncs(env, monkeypatch: pytest.MonkeyPatch):
    ws, git, gh, tool, _ = env

    def broken(_repo):
        msg = 'not logged in'
        raise GhError(msg)

    monkeypatch.setattr(gh, 'current_user', broken)
    result = invoke(ws)
    assert 'Could not check open PRs' in result.output
    assert git.repos[tool].branch == 'master'
