"""Release tasks against a real git repository with a bare origin (offline); GitHub is faked."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from packaging.version import Version

from datoso_dev.io.git import Git
from datoso_dev.pipeline import update_core_extras
from datoso_dev.planner import PackagePlan
from datoso_dev.policy import FloorChange
from datoso_dev.release import Outcome, open_release_pr
from datoso_dev.state import collect_state
from datoso_dev.workspace import discover, load_package
from tests.conftest import write_gitmodules, write_package
from tests.fakes import FakeGh, FakePyPI

pytestmark = pytest.mark.integration


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def seed_repo(isolated_git, tmp_path: Path) -> tuple[Path, Path]:
    """``datoso_seed_x`` 1.0.1 with ``datoso>=1.1.0``, tagged v1.0.1 plus one unreleased commit, pushed to origin."""
    origin = tmp_path / 'origin.git'
    sh(tmp_path, 'init', '-q', '--bare', '-b', 'master', str(origin))
    work_root = tmp_path / 'ws'
    work_root.mkdir()
    sh(work_root, 'clone', '-q', str(origin), 'datoso_seed_x')
    repo = work_root / 'datoso_seed_x'
    write_package(work_root, 'datoso_seed_x', '1.0.1', ['datoso>=1.1.0'])
    sh(repo, 'add', '.')
    sh(repo, 'commit', '-q', '-m', 'initial')
    sh(repo, 'tag', 'v1.0.1')
    (repo / 'README.md').write_text('docs')
    sh(repo, 'add', 'README.md')
    sh(repo, 'commit', '-q', '-m', 'Improve docs')
    sh(repo, 'push', '-q', '--tags', 'origin', 'master')
    return origin, repo


def test_release_branch_is_pushed_and_master_stays_clean(seed_repo):
    origin, repo = seed_repo
    git = Git()
    pkg = load_package(repo)
    state = collect_state(pkg, git, FakePyPI({'datoso-seed-x': '1.0.1'}), fetch=True)
    floor = FloorChange(pkg.name, 'datoso', 'datoso', Version('1.1.0'), Version('1.1.1'))
    item = PackagePlan(pkg.name, pkg.version, Version('1.0.1'), Version('1.0.2'), 1, [floor])
    gh = FakeGh()

    result = open_release_pr(item, pkg, git, gh, since=state.last_tag)

    assert result.outcome is Outcome.OPENED, result.detail
    # origin has the release branch with exactly one commit on top of master
    assert sh(origin, 'log', '--format=%s', 'master..release/v1.0.2').splitlines() == ['Release datoso_seed_x v1.0.2']
    assert "__version__ = '1.0.2'" in sh(origin, 'show', 'release/v1.0.2:src/datoso_seed_x/__init__.py')
    assert '"datoso>=1.1.1"' in sh(origin, 'show', 'release/v1.0.2:pyproject.toml')
    # local master untouched and clean
    assert git.current_branch(repo) == 'master'
    assert git.changed_files(repo) == []
    assert "__version__ = '1.0.1'" in (repo / 'src/datoso_seed_x/__init__.py').read_text()
    # the PR body lists the unreleased commit
    assert '- Improve docs' in gh.created[0]['body']
    # re-running is a no-op once the PR exists
    assert open_release_pr(item, pkg, git, gh, since=state.last_tag).outcome is Outcome.ALREADY_OPEN


def test_resume_after_push_when_pr_creation_failed(seed_repo):
    _, repo = seed_repo
    git = Git()
    pkg = load_package(repo)
    item = PackagePlan(pkg.name, pkg.version, Version('1.0.1'), Version('1.0.2'), 1)
    first = open_release_pr(item, pkg, git, FakeGh(fail=True), since='v1.0.1')
    assert first.outcome is Outcome.ERROR
    gh = FakeGh()
    second = open_release_pr(item, pkg, git, gh, since='v1.0.1')
    assert second.outcome is Outcome.OPENED
    assert len(gh.created) == 1
    assert git.changed_files(repo) == []


def test_core_extras_commit_lands_on_origin_master(isolated_git, tmp_path: Path):
    origin = tmp_path / 'core.git'
    sh(tmp_path, 'init', '-q', '--bare', '-b', 'master', str(origin))
    ws_root = tmp_path / 'ws'
    ws_root.mkdir()
    sh(ws_root, 'clone', '-q', str(origin), 'datoso')
    core = ws_root / 'datoso'
    write_package(ws_root, 'datoso', '1.1.1', optional={'redump': ['datoso-seed-redump>=1.0.1']})
    write_package(ws_root, 'datoso_seed_redump', '1.0.2', ['datoso>=1.1.1'])
    write_gitmodules(ws_root, ['datoso', 'datoso_seed_redump'])
    sh(core, 'add', '.')
    sh(core, 'commit', '-q', '-m', 'initial')
    sh(core, 'push', '-q', '-u', 'origin', 'master')

    result = update_core_extras(discover(ws_root), Git(), {'datoso_seed_redump': Version('1.0.2')})
    assert result is not None
    assert result.outcome is Outcome.UPDATED, result.detail
    assert sh(core, 'status', '--porcelain') == ''
    assert sh(origin, 'log', '-1', '--format=%s', 'master') == 'Update extras floors: datoso-seed-redump>=1.0.2'
    assert '"datoso-seed-redump>=1.0.2"' in sh(origin, 'show', 'master:pyproject.toml')
