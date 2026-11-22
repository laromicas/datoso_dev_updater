"""Tests for `datoso-dev sync` logic (written first, TDD)."""

from __future__ import annotations

from pathlib import Path

from datoso_dev.sync import Outcome, sync_all, sync_repo

from .fakes import FakeGit, FakeRepo

REPO = Path('/ws/datoso_seed_x')


def _sync(repo: FakeRepo, **kwargs):
    git = FakeGit({REPO: repo})
    return sync_repo(REPO, git, **kwargs), git


def test_clean_master_behind_is_pulled():
    result, git = _sync(FakeRepo(behind=3))
    assert result.outcome is Outcome.UPDATED
    assert result.detail == 'pulled 3 commit(s)'
    assert git.called('fetch') == [REPO]
    assert git.called('pull_rebase') == [REPO]


def test_clean_master_in_sync_is_up_to_date():
    result, git = _sync(FakeRepo())
    assert result.outcome is Outcome.UP_TO_DATE
    assert git.called('pull_rebase') == []


def test_unpushed_commits_are_reported():
    result, _ = _sync(FakeRepo(ahead=2))
    assert result.outcome is Outcome.UP_TO_DATE
    assert '2 unpushed commit(s)' in result.detail


def test_dirty_repo_is_skipped_untouched():
    result, git = _sync(FakeRepo(branch='feature', changed=['x.py'], behind=1, branches={'master', 'feature'}))
    assert result.outcome is Outcome.SKIPPED
    assert result.detail == '1 uncommitted file(s)'
    assert git.called('switch') == []
    assert git.called('pull_rebase') == []


def test_other_branch_is_switched_to_master_then_pulled():
    repo = FakeRepo(branch='1.0.2', behind=1, branches={'master', '1.0.2'})
    result, git = _sync(repo)
    assert result.switched_from == '1.0.2'
    assert repo.branch == 'master'
    assert result.outcome is Outcome.UPDATED
    assert git.calls.index(('switch', REPO)) < git.calls.index(('pull_rebase', REPO))


def test_custom_branch():
    repo = FakeRepo(branch='master', branches={'master', 'main'})
    result, _ = _sync(repo, branch='main')
    assert repo.branch == 'main'
    assert result.switched_from == 'master'


def test_no_upstream_is_skipped():
    result, git = _sync(FakeRepo(has_upstream=False))
    assert result.outcome is Outcome.SKIPPED
    assert result.detail == 'no upstream'
    assert git.called('pull_rebase') == []


def test_dry_run_fetches_but_changes_nothing():
    repo = FakeRepo(branch='dev', behind=2, branches={'master', 'dev'})
    result, git = _sync(repo, dry_run=True)
    assert git.called('fetch') == [REPO]
    assert git.called('switch') == []
    assert git.called('pull_rebase') == []
    assert repo.branch == 'dev'
    assert result.outcome is Outcome.UPDATED
    assert result.dry_run is True
    assert result.switched_from == 'dev'


def test_git_error_is_reported():
    result, _ = _sync(FakeRepo(fail_on={'fetch'}))
    assert result.outcome is Outcome.ERROR
    assert 'fetch failed' in result.detail


def test_missing_target_branch_is_error():
    result, _ = _sync(FakeRepo(branch='dev', branches={'dev'}))
    assert result.outcome is Outcome.ERROR
    assert 'no branch master' in result.detail


def test_sync_all_keeps_order_and_isolates_failures():
    paths = [Path(f'/ws/r{i}') for i in range(5)]
    repos = {p: FakeRepo() for p in paths}
    repos[paths[2]].fail_on = {'fetch'}
    results = sync_all(paths, FakeGit(repos))
    assert [r.path for r in results] == paths
    assert [r.outcome for r in results].count(Outcome.ERROR) == 1
