"""Integration tests for the git adapter against real repositories (offline)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from datoso_dev.io.git import Git
from datoso_dev.ports import GitError

pytestmark = pytest.mark.integration


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, name: str, content: str = 'x') -> None:
    (repo / name).write_text(content)
    sh(repo, 'add', name)
    sh(repo, 'commit', '-q', '-m', f'add {name}')


@pytest.fixture
def remote_pair(isolated_git, tmp_path: Path) -> tuple[Path, Path, Path]:
    """A bare origin and two clones (``work`` under test, ``other`` simulating another machine)."""
    origin = tmp_path / 'origin.git'
    sh(tmp_path, 'init', '-q', '--bare', '-b', 'master', str(origin))
    seed = tmp_path / 'seed'
    sh(tmp_path, 'clone', '-q', str(origin), str(seed))
    commit(seed, 'README.md')
    sh(seed, 'push', '-q', 'origin', 'master')
    work = tmp_path / 'work'
    other = tmp_path / 'other'
    sh(tmp_path, 'clone', '-q', str(origin), str(work))
    sh(tmp_path, 'clone', '-q', str(origin), str(other))
    return origin, work, other


def test_branch_and_detached(remote_pair):
    _, work, _ = remote_pair
    git = Git()
    assert git.current_branch(work) == 'master'
    sh(work, 'checkout', '-q', '--detach')
    assert git.current_branch(work) == 'HEAD'


def test_changed_files_respects_gitignore(remote_pair):
    _, work, _ = remote_pair
    git = Git()
    assert git.changed_files(work) == []
    (work / '.gitignore').write_text('*.log\n')
    (work / 'debug.log').write_text('ignored')
    (work / 'README.md').write_text('modified')
    (work / 'new_dir').mkdir()
    (work / 'new_dir' / 'f.py').write_text('')
    assert sorted(git.changed_files(work)) == ['.gitignore', 'README.md', 'new_dir/f.py']


def test_ahead_behind_and_pull(remote_pair):
    _, work, other = remote_pair
    git = Git()
    assert git.ahead_behind(work) == (0, 0)
    commit(work, 'local.txt')
    assert git.ahead_behind(work) == (1, 0)
    commit(other, 'remote.txt')
    sh(other, 'push', '-q', 'origin', 'master')
    git.fetch(work)
    assert git.ahead_behind(work) == (1, 1)
    git.pull_rebase(work)
    assert git.ahead_behind(work) == (1, 0)
    assert (work / 'remote.txt').exists()


def test_no_upstream(remote_pair):
    _, work, _ = remote_pair
    sh(work, 'switch', '-q', '-c', 'local-only')
    assert Git().ahead_behind(work) is None


def test_tags_and_commit_counts(remote_pair):
    _, work, other = remote_pair
    git = Git()
    assert git.count_commits(work, None) == 1
    sh(work, 'tag', 'v1.0.0')
    assert git.count_commits(work, 'v1.0.0') == 0
    commit(work, 'a.txt')
    commit(work, 'b.txt')
    assert git.count_commits(work, 'v1.0.0') == 2
    # tags pushed from elsewhere arrive with fetch
    sh(other, 'tag', 'v0.9.0')
    sh(other, 'push', '-q', 'origin', 'v0.9.0')
    git.fetch(work)
    assert sorted(git.tags(work)) == ['v0.9.0', 'v1.0.0']


def test_empty_repository(isolated_git, tmp_path: Path):
    repo = tmp_path / 'empty'
    sh(tmp_path, 'init', '-q', '-b', 'master', str(repo))
    git = Git()
    assert git.current_branch(repo) == 'master'
    assert git.count_commits(repo, None) == 0
    assert git.ahead_behind(repo) is None
    assert git.tags(repo) == []


def test_switch_and_errors(remote_pair):
    _, work, _ = remote_pair
    git = Git()
    sh(work, 'branch', 'feature')
    git.switch(work, 'feature')
    assert git.current_branch(work) == 'feature'
    with pytest.raises(GitError, match='nope'):
        git.switch(work, 'nope')


def test_missing_executable_raises_git_error(tmp_path: Path):
    with pytest.raises(GitError):
        Git(executable='definitely-not-git').tags(tmp_path)


def test_create_branch_commit_and_push(remote_pair):
    origin, work, _ = remote_pair
    git = Git()
    git.create_branch(work, 'datoso-dev/ci')
    assert git.current_branch(work) == 'datoso-dev/ci'
    (work / '.github' / 'workflows').mkdir(parents=True)
    (work / '.github' / 'workflows' / 'ci.yml').write_text('name: CI\n')
    git.commit_paths(work, ['.github/workflows/ci.yml'], 'CI: add workflow')
    git.push(work, 'datoso-dev/ci', force=True)
    assert sh(origin, 'log', '-1', '--format=%s', 'datoso-dev/ci') == 'CI: add workflow'
    assert git.ahead_behind(work) == (0, 0)  # upstream was set
    git.switch(work, 'master')
    assert not (work / '.github').exists()


def test_create_branch_resets_existing_branch_and_force_push(remote_pair):
    origin, work, _ = remote_pair
    git = Git()
    git.create_branch(work, 'topic')
    commit(work, 'first.txt')
    git.push(work, 'topic')
    git.switch(work, 'master')
    git.create_branch(work, 'topic')  # back at master, the old commit is dropped
    commit(work, 'second.txt')
    with pytest.raises(GitError):
        git.push(work, 'topic')  # diverged: plain push is rejected
    git.push(work, 'topic', force=True)
    assert sh(origin, 'log', '--format=%s', 'topic').splitlines() == ['add second.txt', 'add README.md']


def test_commit_paths_only_commits_given_paths(remote_pair):
    _, work, _ = remote_pair
    git = Git()
    (work / 'a.txt').write_text('a')
    (work / 'b.txt').write_text('b')
    git.commit_paths(work, ['a.txt'], 'add a')
    assert git.changed_files(work) == ['b.txt']


def test_show_file_at_ref(remote_pair):
    _, work, _ = remote_pair
    git = Git()
    assert git.show_file(work, 'origin/master', 'README.md') == 'x'
    assert git.show_file(work, 'origin/master', 'missing.txt') is None
    assert git.show_file(work, 'origin/no-such-branch', 'README.md') is None


def test_create_branch_from_start_point(remote_pair):
    _, work, other = remote_pair
    commit(other, 'upstream.txt')
    sh(other, 'push', '-q', 'origin', 'master')
    git = Git()
    git.fetch(work)
    git.create_branch(work, 'release/v1.0.1', start='origin/master')
    assert (work / 'upstream.txt').exists()  # started from origin/master, not the stale local master
    assert git.current_branch(work) == 'release/v1.0.1'


def test_log_subjects(remote_pair):
    _, work, _ = remote_pair
    git = Git()
    sh(work, 'tag', 'v1.0.0')
    commit(work, 'a.txt')
    commit(work, 'b.txt')
    assert git.log_subjects(work, since='v1.0.0', rev='HEAD') == ['add b.txt', 'add a.txt']
    assert git.log_subjects(work, since=None, rev='HEAD') == ['add b.txt', 'add a.txt', 'add README.md']


def test_move_master_commits_to_a_branch(remote_pair):
    """What `pr` does with commits made on master: branch at them, then master back to origin/master."""
    _, work, _ = remote_pair
    commit(work, 'feature.txt')
    git = Git()
    assert not git.branch_exists(work, 'feat/x')
    git.create_branch(work, 'feat/x')
    git.force_branch(work, 'master', 'origin/master')
    assert git.branch_exists(work, 'feat/x')
    assert git.log_subjects(work, 'origin/master', 'HEAD') == ['add feature.txt']
    assert sh(work, 'rev-parse', 'master') == sh(work, 'rev-parse', 'origin/master')
    git.push(work, 'feat/x')
    sh(work, 'switch', '-q', 'master')
    sh(work, 'branch', '-q', '-D', 'feat/x')
    assert git.branch_exists(work, 'feat/x')  # still on origin


def test_rev_parse(remote_pair):
    _, work, _ = remote_pair
    assert Git().rev_parse(work, 'origin/master') == sh(work, 'rev-parse', 'origin/master')
    with pytest.raises(GitError):
        Git().rev_parse(work, 'origin/nope')


def test_committed_pointer_reads_the_recorded_submodule_commit(remote_pair):
    _, work, _ = remote_pair
    sha = sh(work, 'rev-parse', 'HEAD')
    sh(work, 'update-index', '--add', '--cacheinfo', f'160000,{sha},sub')  # a gitlink, as `git submodule add` makes
    sh(work, 'commit', '-q', '-m', 'add sub')
    git = Git()
    assert git.committed_pointer(work, 'sub') == sha
    assert git.committed_pointer(work, 'README.md') is None  # a blob, not a submodule
    assert git.committed_pointer(work, 'missing') is None
