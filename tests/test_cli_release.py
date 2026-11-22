"""CLI tests for `release`, `bump` and `deps` (typer CliRunner, fake git/GitHub/PyPI; written first, TDD)."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from datoso_dev import cli
from datoso_dev.workspace import discover

from .fakes import FakeGh, FakeGit, FakePrompter, FakePyPI, FakeRepo

runner = CliRunner()
REDUMP = 'datoso_seed_redump'


@pytest.fixture
def env(sample_workspace: Path, monkeypatch: pytest.MonkeyPatch):
    ws = discover(sample_workspace)
    repos = {path: FakeRepo() for path in ws.submodule_paths}
    for pkg in ws.packages.values():
        repos[pkg.path] = FakeRepo(tags=[f'v{pkg.version}'], commits_since={f'v{pkg.version}': 0})
    repos[ws.packages[REDUMP].path].commits_since = {'v1.0.1': 2}
    git, gh = FakeGit(repos), FakeGh()
    pypi = FakePyPI({pkg.dist_name: str(pkg.version) for pkg in ws.packages.values()})
    monkeypatch.setattr(cli, 'make_git', lambda: git)
    monkeypatch.setattr(cli, 'make_gh', lambda: gh)
    monkeypatch.setattr(cli, 'make_pypi', lambda: pypi)
    monkeypatch.setattr(cli, 'is_interactive', lambda: False)
    monkeypatch.delenv(cli.WORKSPACE_ENV, raising=False)
    monkeypatch.setattr(cli, 'console', Console(width=250))  # wide enough that plan rows don't wrap
    return ws, git, gh


def invoke(ws, *args: str, **kwargs):
    return runner.invoke(cli.app, [*args, '-w', str(ws.root)], terminal_width=200, **kwargs)


# --------------------------------------------------------------------------- release, non-interactive


def test_release_dry_run_shows_plan_and_changes_nothing(env):
    ws, git, gh = env
    result = invoke(ws, 'release', '--only', 'redump', '--bump', 'patch', '--dry-run')
    assert result.exit_code == 0, result.output
    assert 'datoso_seed_redump' in result.output
    assert '1.0.1 → 1.0.2' in result.output
    assert 'datoso>=1.1.0 → datoso>=1.1.1' in result.output
    assert '[redump] datoso-seed-redump>=1.0.1 → datoso-seed-redump>=1.0.2' in result.output  # core extras
    assert git.commits == []
    assert gh.created == []


def test_release_yes_opens_step_one_prs(env):
    ws, _, gh = env
    result = invoke(ws, 'release', '--only', 'datoso,redump', '--bump', 'patch', '--yes')
    assert result.exit_code == 0, result.output
    assert [pr['repo'].name for pr in gh.created] == ['datoso']  # redump waits for core to be published
    assert 'waits for step 1' in result.output
    assert 'https://github.com/owner/datoso/pull/1' in result.output


def test_release_non_interactive_needs_only_bump_and_yes(env):
    ws, git, _ = env
    for args in (['release'], ['release', '--only', 'redump'], ['release', '--only', 'redump', '--bump', 'patch']):
        result = invoke(ws, *args)
        assert result.exit_code == 2, (args, result.output)
        assert 'not a terminal' in result.output
    assert git.commits == []


def test_release_blocked_plan_opens_nothing(env):
    ws, git, gh = env
    git.repos[ws.packages[REDUMP].path].changed = ['wip.py']
    result = invoke(ws, 'release', '--only', 'redump', '--bump', 'patch', '--yes')
    assert result.exit_code == 1
    assert '1 uncommitted file(s)' in result.output
    assert gh.created == []


def test_release_template_is_refused(env):
    ws, _, gh = env
    result = invoke(ws, 'release', '--only', 'base', '--bump', 'patch', '--yes')
    assert result.exit_code == 2
    assert 'datoso_seed_base is a template' in result.output
    assert gh.created == []


def test_release_unknown_package(env):
    ws, _, _ = env
    result = invoke(ws, 'release', '--only', 'nope', '--bump', 'patch', '--yes')
    assert result.exit_code == 2
    assert 'unknown package: nope' in result.output


# --------------------------------------------------------------------------- release, interactive


def test_release_wizard_flow(env, monkeypatch: pytest.MonkeyPatch):
    ws, _, gh = env
    patch = lambda options: next(o.value for o in options if o.label.startswith('patch'))  # noqa: E731
    prompter = FakePrompter([[REDUMP], patch, True])
    monkeypatch.setattr(cli, 'is_interactive', lambda: True)
    monkeypatch.setattr(cli, 'make_prompter', lambda: prompter)
    result = invoke(ws, 'release')
    assert result.exit_code == 0, result.output
    assert [pr['repo'].name for pr in gh.created] == [REDUMP]
    assert prompter.asked[-1][1] == 'Open 1 release PR(s)?'


def test_release_wizard_decline(env, monkeypatch: pytest.MonkeyPatch):
    ws, git, gh = env
    monkeypatch.setattr(cli, 'is_interactive', lambda: True)
    monkeypatch.setattr(cli, 'make_prompter', lambda: FakePrompter([[REDUMP], lambda o: o[0].value, None]))
    result = invoke(ws, 'release')
    assert result.exit_code == 0
    assert gh.created == []
    assert git.commits == []


def test_release_wizard_cancel(env, monkeypatch: pytest.MonkeyPatch):
    ws, _, gh = env
    monkeypatch.setattr(cli, 'is_interactive', lambda: True)
    monkeypatch.setattr(cli, 'make_prompter', lambda: FakePrompter([None]))
    result = invoke(ws, 'release')
    assert result.exit_code == 0
    assert gh.created == []


# --------------------------------------------------------------------------- bump / deps


def test_bump_edits_version_only(env):
    ws, git, _ = env
    result = invoke(ws, 'bump', 'redump', 'minor')
    assert result.exit_code == 0, result.output
    assert 'datoso_seed_redump: 1.0.1 → 1.1.0' in result.output
    assert "__version__ = '1.1.0'" in (ws.packages[REDUMP].path / 'src' / REDUMP / '__init__.py').read_text()
    assert git.commits == []


def test_deps_reports_and_fixes(env):
    ws, git, _ = env
    result = invoke(ws, 'deps')
    assert result.exit_code == 1  # violations found
    assert 'datoso_seed_redump' in result.output
    assert 'datoso>=1.1.0 → datoso>=1.1.1' in result.output
    fixed = invoke(ws, 'deps', '--fix')
    assert fixed.exit_code == 0, fixed.output
    assert '"datoso>=1.1.1"' in (ws.packages[REDUMP].path / 'pyproject.toml').read_text()
    assert invoke(ws, 'deps').exit_code == 0  # nothing left once the working tree is reloaded
    assert git.commits == []
