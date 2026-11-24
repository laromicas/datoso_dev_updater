"""CLI tests for `datoso-dev pr`, with fake git/GitHub/PyPI."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from datoso_dev import cli
from datoso_dev.workspace import discover

from .fakes import FakeGh, FakeGit, FakePyPI, FakeRepo

runner = CliRunner()
BRANCH = 'feat/x'


@pytest.fixture
def fakes(sample_workspace: Path, monkeypatch: pytest.MonkeyPatch):
    ws = discover(sample_workspace)
    repos = {pkg.path: FakeRepo() for pkg in ws.packages.values()}
    seed = FakeRepo(branch=BRANCH, subjects=['Seed change'], has_upstream=False)
    repos[ws.packages['datoso_seed_redump'].path] = seed
    git, gh = FakeGit(repos), FakeGh()
    pypi = FakePyPI({pkg.dist_name: str(pkg.version) for pkg in ws.packages.values()})
    monkeypatch.setattr(cli, 'make_git', lambda: git)
    monkeypatch.setattr(cli, 'make_gh', lambda: gh)
    monkeypatch.setattr(cli, 'make_pypi', lambda: pypi)
    monkeypatch.setattr(cli, 'is_interactive', lambda: False)
    monkeypatch.delenv(cli.WORKSPACE_ENV, raising=False)
    return ws, git, gh


def invoke(ws, *args: str, **kwargs):
    return runner.invoke(cli.app, ['pr', '-w', str(ws.root), *args], **kwargs)


def test_dry_run_changes_nothing(fakes):
    ws, git, gh = fakes
    result = invoke(ws, '--dry-run')
    assert result.exit_code == 0, result.output
    assert 'open PR' in result.output
    assert git.pushes == []
    assert gh.created == []


def test_title_and_yes_open_the_pr(fakes):
    ws, git, gh = fakes
    result = invoke(ws, '--title', 'Seed work', '--body', 'Why', '--yes')
    assert result.exit_code == 0, result.output
    assert git.pushes == [(ws.packages['datoso_seed_redump'].path, BRANCH, False)]
    assert gh.created[0]['title'] == 'Seed work'
    assert gh.created[0]['body'].startswith('Why\n\nCommits:\n- Seed change')
    assert 'PR opened' in result.output
    assert gh.created[0]['url'] in result.output


def test_new_pr_without_title_needs_a_terminal(fakes):
    ws, _, gh = fakes
    result = invoke(ws, '--yes')
    assert result.exit_code == 2
    assert '--title is needed' in result.output
    assert gh.created == []


def test_editor_writes_the_text_then_confirms(fakes, monkeypatch: pytest.MonkeyPatch):
    ws, _, gh = fakes
    monkeypatch.setattr(cli, 'is_interactive', lambda: True)

    def edit(path: Path) -> None:
        text = path.read_text(encoding='utf-8')
        assert text.startswith('Seed change\n')
        path.write_text(text.replace('=== datoso_seed_redump\n', '=== datoso_seed_redump\nSeed note\n'))

    monkeypatch.setattr(cli, 'run_editor', edit)
    result = invoke(ws, input='y\n')
    assert result.exit_code == 0, result.output
    assert gh.created[0]['title'] == 'Seed change'
    assert gh.created[0]['body'].startswith('Seed note\n\nCommits:')


def test_empty_title_in_editor_cancels(fakes, monkeypatch: pytest.MonkeyPatch):
    ws, _, gh = fakes
    monkeypatch.setattr(cli, 'is_interactive', lambda: True)
    monkeypatch.setattr(cli, 'run_editor', lambda path: path.write_text(''))
    result = invoke(ws)
    assert result.exit_code == 1
    assert 'empty title' in result.output
    assert gh.created == []


def test_editor_failure(fakes, monkeypatch: pytest.MonkeyPatch):
    ws, _, _ = fakes
    monkeypatch.setattr(cli, 'is_interactive', lambda: True)

    def broken(_path: Path) -> None:
        msg = 'no editor'
        raise OSError(msg)

    monkeypatch.setattr(cli, 'run_editor', broken)
    result = invoke(ws)
    assert result.exit_code == 1
    assert 'editor failed' in result.output


def test_declining_the_confirmation_aborts(fakes):
    ws, git, _ = fakes
    result = invoke(ws, '--title', 'T', input='n\n')
    assert result.exit_code == 1
    assert git.pushes == []


def test_nothing_to_propose(fakes):
    ws, git, _ = fakes
    git.repos[ws.packages['datoso_seed_redump'].path].branch = 'master'
    git.repos[ws.packages['datoso_seed_redump'].path].subjects = []
    result = invoke(ws)
    assert result.exit_code == 0
    assert 'No feature branches' in result.output


def test_up_to_date_pr_does_nothing(fakes):
    ws, git, gh = fakes
    path = ws.packages['datoso_seed_redump'].path
    git.repos[path].has_upstream = True
    gh.open_prs[(path, BRANCH)] = 'https://github.com/o/r/pull/1'
    result = invoke(ws, 'redump')
    assert result.exit_code == 0
    assert 'Nothing to push or open' in result.output


def test_errors_exit_non_zero(fakes):
    ws, git, gh = fakes
    git.repos[ws.packages['datoso'].path] = FakeRepo(fail_on={'fetch'})
    gh.fail = True
    result = invoke(ws, '--title', 'T', '--yes')
    assert result.exit_code == 1
    assert gh.created == []
    assert 'error' in result.output


def test_unknown_package(fakes):
    ws, _, _ = fakes
    result = invoke(ws, 'nope')
    assert result.exit_code == 2
