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
