"""CLI tests via typer's CliRunner, with fake git/PyPI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from datoso_dev import __version__, cli
from datoso_dev.workspace import discover

from .fakes import FakeGit, FakePyPI, FakeRepo

runner = CliRunner()


@pytest.fixture
def fakes(sample_workspace: Path, monkeypatch: pytest.MonkeyPatch):
    ws = discover(sample_workspace)
    repos = {path: FakeRepo() for path in ws.submodule_paths}
    for pkg in ws.packages.values():
        repos[pkg.path] = FakeRepo(tags=[f'v{pkg.version}'], commits_since={f'v{pkg.version}': 0})
    repos[ws.packages['datoso_seed_redump'].path].commits_since = {'v1.0.1': 2}
    git = FakeGit(repos)
    pypi = FakePyPI({pkg.dist_name: str(pkg.version) for pkg in ws.packages.values()})
    monkeypatch.setattr(cli, 'make_git', lambda: git)
    monkeypatch.setattr(cli, 'make_pypi', lambda: pypi)
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
