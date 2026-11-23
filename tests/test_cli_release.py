"""CLI tests for `release`, `bump` and `deps` (typer CliRunner, fake git/GitHub/PyPI; written first, TDD)."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from datoso_dev import cli
from datoso_dev.ports import Checks
from datoso_dev.workspace import discover

from .fakes import FakeGh, FakeGit, FakePrompter, FakePyPI, FakeRepo

runner = CliRunner()
REDUMP = 'datoso_seed_redump'


class Env(tuple):  # noqa: SLOT001 - (ws, git, gh), unpackable as before, plus the fake PyPI
    pypi: FakePyPI


@pytest.fixture
def env(sample_workspace: Path, monkeypatch: pytest.MonkeyPatch):
    ws = discover(sample_workspace)
    repos = {path: FakeRepo() for path in [ws.root, *ws.submodule_paths]}
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
    result = Env((ws, git, gh))
    result.pypi = pypi
    return result


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
    assert 'Merge the PR(s), then run `datoso-dev release` again' in result.output


def test_release_dry_run_shows_what_each_package_needs(env):
    ws, _, gh = env
    gh.merged_prs[(ws.packages[REDUMP].path, 'release/v1.0.2')] = 'https://github.com/owner/r/pull/4'
    result = invoke(ws, 'release', '--only', 'redump,enhanced', '--bump', 'patch', '--dry-run')
    assert result.exit_code == 0, result.output
    assert 'tag + publish release (PR merged: https://github.com/owner/r/pull/4)' in result.output
    assert 'open release PR' in result.output
    assert gh.releases_created == []


def test_release_after_merge_publishes_and_waits_for_pypi(env):
    ws, _, gh = env
    redump = ws.packages[REDUMP]
    gh.merged_prs[(redump.path, 'release/v1.0.2')] = 'https://github.com/owner/r/pull/4'
    env.pypi.appears_after[(redump.dist_name, '1.0.2')] = 0  # listed once the publish workflow uploads it
    result = invoke(ws, 'release', '--only', 'redump', '--bump', 'patch', '--yes')
    assert result.exit_code == 0, result.output
    assert [(r['tag'], r['target']) for r in gh.releases_created] == [('v1.0.2', 'sha-of-4')]
    assert gh.created == []  # no duplicate PR
    assert '1.0.2 on PyPI' in result.output
    assert 'https://github.com/owner/datoso_seed_redump/releases/tag/v1.0.2' in result.output
    assert 'datoso-dev sync' in result.output  # next step


def test_release_no_wait(env):
    ws, _, gh = env
    gh.merged_prs[(ws.packages[REDUMP].path, 'release/v1.0.2')] = 'https://github.com/owner/r/pull/4'
    result = invoke(ws, 'release', '--only', 'redump', '--bump', 'patch', '--yes', '--no-wait')
    assert result.exit_code == 0, result.output
    assert env.pypi.polls == []
    assert 'PyPI upload not awaited' in result.output


def test_release_with_only_open_prs_has_nothing_to_do(env):
    ws, _, gh = env
    gh.open_prs[(ws.packages[REDUMP].path, 'release/v1.0.2')] = 'https://github.com/owner/r/pull/9'
    result = invoke(ws, 'release', '--only', 'redump', '--bump', 'patch', '--yes')
    assert result.exit_code == 0, result.output
    assert 'Nothing to do now' in result.output
    assert 'https://github.com/owner/r/pull/9' in result.output
    assert gh.created == []


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
    prompter = FakePrompter([[REDUMP], patch, 'step'])
    monkeypatch.setattr(cli, 'is_interactive', lambda: True)
    monkeypatch.setattr(cli, 'make_prompter', lambda: prompter)
    result = invoke(ws, 'release')
    assert result.exit_code == 0, result.output
    assert [pr['repo'].name for pr in gh.created] == [REDUMP]
    kind, message, options = prompter.asked[-1]
    assert (kind, message) == ('select', 'What should this run do?')
    assert [o.value for o in options] == ['full', 'step']
    assert options[1].label == 'Open 1 release PR(s), then stop (you merge on GitHub)'


def test_release_wizard_question_names_the_actions(env, monkeypatch: pytest.MonkeyPatch):
    ws, _, gh = env
    gh.merged_prs[(ws.packages[REDUMP].path, 'release/v1.0.2')] = 'https://github.com/owner/r/pull/4'
    env.pypi.appears_after[(ws.packages[REDUMP].dist_name, '1.0.2')] = 0
    patch = lambda options: next(o.value for o in options if o.label.startswith('patch'))  # noqa: E731
    prompter = FakePrompter([[REDUMP, 'datoso_seed_enhanced'], patch, patch, 'step'])
    monkeypatch.setattr(cli, 'is_interactive', lambda: True)
    monkeypatch.setattr(cli, 'make_prompter', lambda: prompter)
    result = invoke(ws, 'release')
    assert result.exit_code == 0, result.output
    assert prompter.asked[-1][2][1].label.startswith('Open 1 release PR(s) and publish 1 release(s), then stop')
    assert len(gh.releases_created) == 1
    assert len(gh.created) == 1


def test_release_wizard_full_release(env, monkeypatch: pytest.MonkeyPatch):
    ws, git, gh = env
    redump = ws.packages[REDUMP]
    env.pypi.appears_after[(redump.dist_name, '1.0.2')] = 0
    git.repos[ws.root].changed = [REDUMP, 'datoso']
    patch = lambda options: next(o.value for o in options if o.label.startswith('patch'))  # noqa: E731
    prompter = FakePrompter([[REDUMP], patch, 'full'])
    monkeypatch.setattr(cli, 'is_interactive', lambda: True)
    monkeypatch.setattr(cli, 'make_prompter', lambda: prompter)
    result = invoke(ws, 'release')
    assert result.exit_code == 0, result.output
    assert gh.merged == ['https://github.com/owner/datoso_seed_redump/pull/1']
    assert [r['tag'] for r in gh.releases_created] == ['v1.0.2']
    assert 'Update extras floors: datoso-seed-redump>=1.0.2' in result.output
    assert 'Release: datoso_seed_redump 1.0.2; core extras floors' in result.output
    assert (ws.root, 'master', False) in git.pushes


def test_release_yes_merge_is_the_full_release(env):
    ws, _, gh = env
    env.pypi.appears_after[(ws.packages[REDUMP].dist_name, '1.0.2')] = 0
    result = invoke(ws, 'release', '--only', 'redump', '--bump', 'patch', '--yes', '--merge')
    assert result.exit_code == 0, result.output
    assert len(gh.merged) == 1
    assert len(gh.releases_created) == 1


def test_release_full_ci_failure_exits_1_and_says_how_to_continue(env):
    ws, _, gh = env
    gh.checks['https://github.com/owner/datoso_seed_redump/pull/1'] = [(Checks.FAILED, 'check: https://x')]
    result = invoke(ws, 'release', '--only', 'redump', '--bump', 'patch', '--yes', '--merge')
    assert result.exit_code == 1
    assert 'CI failed' in result.output
    assert 'run `datoso-dev release` again' in result.output
    assert gh.releases_created == []


def test_release_wizard_escape_on_the_mode_goes_back_to_the_versions(env, monkeypatch: pytest.MonkeyPatch):
    ws, _, gh = env
    by = lambda prefix: lambda options: next(o.value for o in options if o.label.startswith(prefix))  # noqa: E731
    prompter = FakePrompter([[REDUMP], by('patch'), None, by('minor'), 'step'])
    monkeypatch.setattr(cli, 'is_interactive', lambda: True)
    monkeypatch.setattr(cli, 'make_prompter', lambda: prompter)
    result = invoke(ws, 'release')
    assert result.exit_code == 0, result.output
    assert [m for _, m, _ in prompter.asked] == [
        'Packages to release',
        'datoso_seed_redump 1.0.1: release as',
        'What should this run do?',
        'datoso_seed_redump 1.0.1: release as',
        'What should this run do?',
    ]
    assert [pr['head'] for pr in gh.created] == ['release/v1.1.0']


def test_release_wizard_decline(env, monkeypatch: pytest.MonkeyPatch):
    ws, git, gh = env
    monkeypatch.setattr(cli, 'is_interactive', lambda: True)
    monkeypatch.setattr(cli, 'make_prompter', lambda: FakePrompter([[REDUMP], lambda o: o[0].value, None, None, None]))
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


# --------------------------------------------------------------------------- notes


def fake_editor(new_text: str | None):
    """Replace the file's content (None: leave it unchanged) and record what the editor was shown."""
    seen = []

    def edit(path: Path) -> None:
        seen.append(path.read_text(encoding='utf-8'))
        if new_text is not None:
            path.write_text(new_text, encoding='utf-8')

    return edit, seen


def test_notes_edits_the_release_body(env, monkeypatch: pytest.MonkeyPatch):
    ws, _, gh = env
    redump = ws.packages[REDUMP]
    gh.releases[(redump.path, 'v1.0.1')] = 'https://github.com/owner/r/releases/tag/v1.0.1'
    gh.notes[(redump.path, 'v1.0.1')] = '## What changed\n* Fix fetch'
    edit, seen = fake_editor('## Highlights\nFaster fetch.\n')
    monkeypatch.setattr(cli, 'run_editor', edit)
    result = invoke(ws, 'notes', 'redump')  # defaults to the latest tag
    assert result.exit_code == 0, result.output
    assert seen == ['## What changed\n* Fix fetch\n']
    assert gh.notes[(redump.path, 'v1.0.1')] == '## Highlights\nFaster fetch.\n'
    assert 'v1.0.1 notes updated' in result.output


def test_notes_unchanged_is_not_uploaded(env, monkeypatch: pytest.MonkeyPatch):
    ws, _, gh = env
    redump = ws.packages[REDUMP]
    gh.releases[(redump.path, 'v1.0.0')] = 'u'
    gh.notes[(redump.path, 'v1.0.0')] = 'old'
    edit, _ = fake_editor(None)
    monkeypatch.setattr(cli, 'run_editor', edit)
    result = invoke(ws, 'notes', 'redump', '1.0.0')
    assert result.exit_code == 0, result.output
    assert 'unchanged' in result.output
    assert gh.notes[(redump.path, 'v1.0.0')] == 'old'


def test_notes_without_release(env):
    ws, _, _ = env
    result = invoke(ws, 'notes', 'redump', '9.9.9')
    assert result.exit_code == 1
    assert 'no GitHub Release v9.9.9' in result.output
