"""CLI tests via typer's CliRunner, with fake git/PyPI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from datoso_dev import __version__, cli
from datoso_dev.ci import WORKFLOW_PATH, render_ci
from datoso_dev.ci_menu import Action
from datoso_dev.workspace import discover

from .fakes import FakeGh, FakeGit, FakePrompter, FakePyPI, FakeRepo

runner = CliRunner()


@pytest.fixture
def fakes(sample_workspace: Path, monkeypatch: pytest.MonkeyPatch):
    ws = discover(sample_workspace)
    repos = {path: FakeRepo() for path in ws.submodule_paths}
    for pkg in ws.packages.values():
        repos[pkg.path] = FakeRepo(tags=[f'v{pkg.version}'], commits_since={f'v{pkg.version}': 0})
    repos[ws.packages['datoso_seed_redump'].path].commits_since = {'v1.0.1': 2}
    git = FakeGit(repos)
    gh = FakeGh()
    pypi = FakePyPI({pkg.dist_name: str(pkg.version) for pkg in ws.packages.values()})
    monkeypatch.setattr(cli, 'make_git', lambda: git)
    monkeypatch.setattr(cli, 'make_pypi', lambda: pypi)
    monkeypatch.setattr(cli, 'make_gh', lambda: gh)
    monkeypatch.delenv(cli.WORKSPACE_ENV, raising=False)
    return ws, git, pypi


def test_version():
    result = runner.invoke(cli.app, ['--version'])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_status_table(fakes):
    ws, git, _ = fakes
    result = runner.invoke(cli.app, ['status', '-w', str(ws.root)], terminal_width=200)
    assert result.exit_code == 0, result.output
    assert 'datoso_seed_redump' in result.output
    assert '1 package(s) need a release: datoso_seed_redump' in result.output
    assert git.called('fetch')  # fetch is the default


def test_status_json_and_no_fetch(fakes):
    ws, git, _ = fakes
    result = runner.invoke(cli.app, ['status', '-w', str(ws.root), '--json', '--no-fetch'])
    assert result.exit_code == 0, result.output
    data = {row['package']: row for row in json.loads(result.output)}
    assert data['datoso_seed_redump']['status'] == 'needs release'
    assert data['datoso_seed_redump']['commits_since_tag'] == 2
    assert data['datoso_seed_mameredump']['status'] == 'skipped'
    assert git.called('fetch') == []


def test_status_offline_skips_fetch_and_pypi(fakes):
    ws, git, pypi = fakes
    result = runner.invoke(cli.app, ['status', '-w', str(ws.root), '--offline', '--json'])
    assert result.exit_code == 0
    assert git.called('fetch') == []
    assert pypi.queried == []


def test_status_exit_code_on_error(fakes):
    ws, git, _ = fakes
    git.repos[ws.packages['datoso'].path].fail_on = {'tags'}
    result = runner.invoke(cli.app, ['status', '-w', str(ws.root)], terminal_width=200)
    assert result.exit_code == 1
    assert 'could not be read: datoso' in result.output


def test_workspace_from_env(fakes, monkeypatch: pytest.MonkeyPatch):
    ws, _, _ = fakes
    monkeypatch.setenv(cli.WORKSPACE_ENV, str(ws.root))
    assert runner.invoke(cli.app, ['status', '--json', '--offline']).exit_code == 0


def test_workspace_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(cli.WORKSPACE_ENV, raising=False)
    result = runner.invoke(cli.app, ['status', '-w', str(tmp_path)])
    assert result.exit_code == 2
    assert 'No datoso workspace' in result.output


def test_sync_covers_all_submodules_including_tool_repo(fakes):
    ws, git, _ = fakes
    result = runner.invoke(cli.app, ['sync', '-w', str(ws.root)], terminal_width=200)
    assert result.exit_code == 0, result.output
    assert sorted(git.called('fetch')) == sorted(ws.submodule_paths)
    assert 'datoso_dev_updater' in result.output


def test_sync_dry_run_and_error_exit(fakes):
    ws, git, _ = fakes
    git.repos[ws.packages['datoso_seed_redump'].path].fail_on = {'fetch'}
    result = runner.invoke(cli.app, ['sync', '-w', str(ws.root), '--dry-run'], terminal_width=200)
    assert result.exit_code == 1
    assert 'dry run' in result.output
    assert git.called('pull_rebase') == []


# --------------------------------------------------------------------------- ci


def test_ci_status_lists_targets(fakes):
    ws, git, _ = fakes
    redump = ws.packages['datoso_seed_redump']
    (redump.path / WORKFLOW_PATH).parent.mkdir(parents=True)
    (redump.path / WORKFLOW_PATH).write_text(render_ci(redump))
    result = runner.invoke(cli.app, ['ci', 'status', '-w', str(ws.root)], terminal_width=200)
    assert result.exit_code == 0, result.output
    assert 'datoso_seed_redump' in result.output
    assert 'up to date' in result.output
    assert 'missing' in result.output
    assert 'datoso_seed_mameredump' not in result.output  # skipped package
    assert git.calls == []  # status is offline


def test_ci_sync_dry_run_opens_nothing(fakes):
    ws, git, _ = fakes
    result = runner.invoke(cli.app, ['ci', 'sync', '-w', str(ws.root), '--dry-run'], terminal_width=200)
    assert result.exit_code == 0, result.output
    assert 'dry run' in result.output
    assert 'will open PR' in result.output
    assert git.commits == []


def test_ci_sync_all_asks_before_opening_prs(fakes):
    ws, git, _ = fakes
    result = runner.invoke(cli.app, ['ci', 'sync', '-w', str(ws.root), '--all'], input='n\n', terminal_width=200)
    assert result.exit_code == 1, result.output
    assert 'Open or update 4 PR(s)?' in result.output
    assert git.commits == []


def test_ci_sync_yes_opens_prs_for_selected(fakes, monkeypatch: pytest.MonkeyPatch):
    ws, _, _ = fakes
    gh = FakeGh()
    monkeypatch.setattr(cli, 'make_gh', lambda: gh)
    result = runner.invoke(cli.app, ['ci', 'sync', '-w', str(ws.root), 'redump', '--yes'], terminal_width=200)
    assert result.exit_code == 0, result.output
    assert [pr['repo'].name for pr in gh.created] == ['datoso_seed_redump']
    assert 'https://github.com/owner/datoso_seed_redump/pull/1' in result.output


def test_ci_sync_nothing_to_do(fakes):
    ws, _, _ = fakes
    redump = ws.packages['datoso_seed_redump']
    (redump.path / WORKFLOW_PATH).parent.mkdir(parents=True)
    (redump.path / WORKFLOW_PATH).write_text(render_ci(redump))
    result = runner.invoke(cli.app, ['ci', 'sync', '-w', str(ws.root), 'redump'], terminal_width=200)
    assert result.exit_code == 0, result.output
    assert 'Nothing to do' in result.output


def test_ci_sync_unknown_package(fakes):
    ws, _, _ = fakes
    result = runner.invoke(cli.app, ['ci', 'sync', '-w', str(ws.root), 'nope'])
    assert result.exit_code == 2
    assert 'unknown package: nope' in result.output


def test_ci_sync_error_exit_code(fakes):
    ws, git, _ = fakes
    git.repos[ws.packages['datoso_seed_redump'].path].fail_on = {'push'}
    result = runner.invoke(cli.app, ['ci', 'sync', '-w', str(ws.root), 'redump', '--yes'], terminal_width=200)
    assert result.exit_code == 1
    assert 'error' in result.output


@pytest.fixture
def interactive(monkeypatch: pytest.MonkeyPatch):
    """Pretend to run in a terminal; the returned prompter quits at the first menu."""
    prompter = FakePrompter([Action.QUIT])
    monkeypatch.setattr(cli, 'is_interactive', lambda: True)
    monkeypatch.setattr(cli, 'make_prompter', lambda: prompter)
    return prompter


@pytest.mark.parametrize('argv', [['ci'], ['ci', 'sync']])
def test_ci_without_selection_opens_menu(fakes, interactive, argv: list[str]):
    ws, git, _ = fakes
    result = runner.invoke(cli.app, [*argv, '-w', str(ws.root)], terminal_width=200)
    assert result.exit_code == 0, result.output
    assert interactive.asked[0][1] == 'What do you want to do?'
    assert 'ci workflow status' in result.output
    assert git.commits == []


def test_ci_sync_without_selection_needs_a_terminal(fakes, monkeypatch: pytest.MonkeyPatch):
    ws, git, _ = fakes
    monkeypatch.setattr(cli, 'is_interactive', lambda: False)
    result = runner.invoke(cli.app, ['ci', 'sync', '-w', str(ws.root)])
    assert result.exit_code == 2
    assert 'pass package names or --all' in result.output
    assert git.calls == []


def test_ci_sync_all_and_packages_conflict(fakes):
    ws, _, _ = fakes
    result = runner.invoke(cli.app, ['ci', 'sync', '-w', str(ws.root), '--all', 'redump'])
    assert result.exit_code == 2
    assert 'either package names or --all' in result.output


def test_ci_menu_include_skipped(fakes, interactive):
    ws, _, _ = fakes
    result = runner.invoke(cli.app, ['ci', '-w', str(ws.root), '--include-skipped'], terminal_width=200)
    assert result.exit_code == 0, result.output
    assert 'datoso_seed_mameredump' in result.output
