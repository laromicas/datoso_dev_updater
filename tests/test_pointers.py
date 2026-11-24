"""Tests for `datoso-dev pointers`: commit only moved pointers whose repo is clean, on master and pushed."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from datoso_dev import cli
from datoso_dev.pointers import Pointer, commit_message, plan_pointers, workspace_blocker
from datoso_dev.workspace import discover

from .fakes import FakeGit, FakeRepo

runner = CliRunner()
REDUMP, TDC = 'datoso_seed_redump', 'datoso_plugin_internetarchive'


@pytest.fixture
def ws(sample_workspace: Path):
    return discover(sample_workspace)


def make_git(ws, root: FakeRepo | None = None, **repos: FakeRepo) -> FakeGit:
    states = {path: FakeRepo(head=f'same-{path.name}') for path in ws.submodule_paths}
    states[ws.root] = root or FakeRepo()
    for name, repo in repos.items():
        states[ws.root / name] = repo
    return FakeGit(states)


def moved(**kwargs: object) -> FakeRepo:
    return FakeRepo(head='new', **{'subjects': ['Fix B', 'Add A'], **kwargs})


def root_with(*names: str, **kwargs: object) -> FakeRepo:
    return FakeRepo(changed=list(names), pointers=dict.fromkeys(names, 'old'), **kwargs)


def test_plan_lists_moved_pointers_with_their_commits(ws):
    git = make_git(ws, root_with(REDUMP), **{REDUMP: moved()})
    [pointer] = plan_pointers(ws, git)
    assert (pointer.name, pointer.old, pointer.new, pointer.commits, pointer.blocker) == (
        REDUMP, 'old', 'new', ('Fix B', 'Add A'), '',
    )  # fmt: skip


@pytest.mark.parametrize(
    ('repo', 'blocker'),
    [
        (moved(changed=['x.py']), 'uncommitted changes (commit them in the repo first)'),
        (moved(branch='feat/x'), 'on feat/x, not master (merge it first)'),
        (moved(ahead=2), '2 unpushed commit(s)'),
        (moved(behind=1), 'behind origin by 1 (pull first)'),
        (moved(has_upstream=False), 'no upstream'),
        (FakeRepo(head='old', changed=['x.py']), 'uncommitted changes (commit them in the repo first)'),
        (moved(fail_on={'rev_parse'}), 'rev_parse failed in'),
    ],
)
def test_unsafe_pointers_are_blocked(ws, repo, blocker):
    [pointer] = plan_pointers(ws, make_git(ws, root_with(REDUMP), **{REDUMP: repo}))
    assert pointer.blocker.startswith(blocker)


def test_rewritten_history_is_still_recordable(ws):
    [pointer] = plan_pointers(ws, make_git(ws, root_with(REDUMP), **{REDUMP: moved(fail_on={'log_subjects'})}))
    assert (pointer.commits, pointer.blocker) == ((), '')


def test_commit_message_lists_repos_and_newest_subjects(tmp_path):
    many = Pointer(tmp_path / 'a', commits=('5', '4', '3', '2', '1'))
    none = Pointer(tmp_path / 'b')
    assert commit_message([many, none]) == 'Pointers: a, b\n\n- a: 5; 4; 3 (+2 more)\n- b: moved'


@pytest.mark.parametrize(
    ('root', 'reason'),
    [
        (FakeRepo(branch='feat'), 'the workspace is on feat, not master'),
        (FakeRepo(has_upstream=False), 'the workspace has no upstream'),
        (FakeRepo(behind=3), 'the workspace is behind origin by 3 (pull it first)'),
        (FakeRepo(ahead=1), ''),
    ],
)
def test_workspace_blocker(ws, root, reason):
    assert workspace_blocker(ws, make_git(ws, root)) == reason


# --------------------------------------------------------------------------- CLI


@pytest.fixture
def use(monkeypatch: pytest.MonkeyPatch):
    def _use(git: FakeGit) -> FakeGit:
        monkeypatch.setattr(cli, 'make_git', lambda: git)
        monkeypatch.delenv(cli.WORKSPACE_ENV, raising=False)
        return git

    return _use


def invoke(ws, *args: str, **kwargs):
    return runner.invoke(cli.app, ['pointers', '-w', str(ws.root), *args], **kwargs)


def test_commits_only_the_ready_pointers_and_pushes(ws, use):
    git = use(make_git(ws, root_with(REDUMP, TDC), **{REDUMP: moved(), TDC: moved(branch='feat/x')}))
    result = invoke(ws, '--yes')
    assert result.exit_code == 1, result.output  # one pointer is still blocked
    [(repo, paths, message)] = git.commits
    assert (repo, paths) == (ws.root, [REDUMP])
    assert message.startswith(f'Pointers: {REDUMP}\n\n- {REDUMP}: Fix B; Add A')
    assert git.pushes == [(ws.root, 'master', False)]
    assert 'Committed and pushed' in result.output


def test_asks_before_committing(ws, use):
    git = use(make_git(ws, root_with(REDUMP), **{REDUMP: moved()}))
    assert invoke(ws, input='n\n').exit_code == 1
    assert git.commits == []
    result = invoke(ws, input='y\n')
    assert result.exit_code == 0, result.output
    assert len(git.commits) == 1


def test_dry_run_changes_nothing(ws, use):
    git = use(make_git(ws, root_with(REDUMP), **{REDUMP: moved()}))
    result = invoke(ws, '--dry-run')
    assert result.exit_code == 0
    assert 'dry run' in result.output
    assert git.commits == []


def test_nothing_moved(ws, use):
    use(make_git(ws))
    result = invoke(ws)
    assert result.exit_code == 0
    assert 'Every submodule pointer is committed' in result.output


def test_all_blocked(ws, use):
    git = use(make_git(ws, root_with(REDUMP), **{REDUMP: moved(ahead=1)}))
    result = invoke(ws, '--yes')
    assert result.exit_code == 1
    assert 'No pointer can be committed yet' in result.output
    assert git.commits == []


def test_workspace_not_ready(ws, use):
    git = use(make_git(ws, root_with(REDUMP, branch='feat'), **{REDUMP: moved()}))
    result = invoke(ws, '--yes')
    assert result.exit_code == 1
    assert 'the workspace is on feat' in result.output
    assert git.commits == []


@pytest.mark.parametrize('failing', ['fetch', 'push'])
def test_git_failures(ws, use, failing):
    git = use(make_git(ws, root_with(REDUMP, fail_on={failing}), **{REDUMP: moved()}))
    result = invoke(ws, '--yes')
    assert result.exit_code == 1
    assert f'{failing} failed' in result.output
    assert git.pushes == []
