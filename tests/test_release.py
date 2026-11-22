"""Tests for the release tasks up to "PR opened" (TDD): idempotent, resumable, never leave master dirty."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from packaging.version import Version

from datoso_dev.planner import plan_release
from datoso_dev.release import Outcome, open_release_pr, open_release_prs
from datoso_dev.state import PackageState
from datoso_dev.workspace import discover

from .fakes import FakeGh, FakeGit, FakeRepo

V = Version
REDUMP = 'datoso_seed_redump'
INIT = 'src/datoso_seed_redump/__init__.py'


@pytest.fixture
def ws(sample_workspace: Path):
    return discover(sample_workspace)


def released_states(ws):
    return {
        name: PackageState(
            pkg,
            branch='master',
            ahead=0,
            behind=0,
            last_tag=f'v{pkg.version}',
            last_tag_version=pkg.version,
            pypi_version=pkg.version,
        )
        for name, pkg in ws.packages.items()
    }


@pytest.fixture
def item(ws):
    return plan_release(ws, released_states(ws), {REDUMP: V('1.0.2')}).item(REDUMP)


@pytest.fixture
def redump(ws):
    return ws.packages[REDUMP]


def fakes(redump, **repo):
    return FakeGit({redump.path: FakeRepo(subjects=['Fix fetch', 'Add rules'], **repo)}), FakeGh()


def test_opens_pr_with_bump_and_floors(ws, redump, item):
    git, gh = fakes(redump)
    result = open_release_pr(item, redump, git, gh, since='v1.0.1')
    assert result.outcome is Outcome.OPENED
    assert result.pr_url == 'https://github.com/owner/datoso_seed_redump/pull/1'
    # branch from origin/master, back on master afterwards
    assert git.branch_starts == [(redump.path, 'release/v1.0.2', 'origin/master')]
    assert git.repos[redump.path].branch == 'master'
    # files edited and committed together
    assert "__version__ = '1.0.2'" in (redump.path / INIT).read_text()
    assert '"datoso>=1.1.1"' in (redump.path / 'pyproject.toml').read_text()
    assert git.commits == [(redump.path, [INIT, 'pyproject.toml'], 'Release datoso_seed_redump v1.0.2')]
    assert git.pushes == [(redump.path, 'release/v1.0.2', False)]
    [pr] = gh.created
    assert (pr['base'], pr['head'], pr['title']) == ('master', 'release/v1.0.2', 'Release datoso_seed_redump v1.0.2')
    assert '1.0.1 → 1.0.2' in pr['body']
    assert '`datoso>=1.1.0` → `datoso>=1.1.1`' in pr['body']
    assert '- Fix fetch\n- Add rules' in pr['body']


def test_existing_pr_means_done(redump, item):
    git, gh = fakes(redump)
    gh.open_prs[(redump.path, 'release/v1.0.2')] = 'https://github.com/owner/datoso_seed_redump/pull/9'
    result = open_release_pr(item, redump, git, gh, since='v1.0.1')
    assert result.outcome is Outcome.ALREADY_OPEN
    assert result.pr_url.endswith('/pull/9')
    assert git.commits == []
    assert git.called('create_branch') == []


def test_branch_already_pushed_resumes_at_pr(redump, item):
    """A previous run pushed the bump but failed to open the PR: don't commit or push again."""
    pushed = (redump.path / INIT).read_text().replace("'1.0.1'", "'1.0.2'")
    git, gh = fakes(redump, files={('origin/release/v1.0.2', INIT): pushed})
    result = open_release_pr(item, redump, git, gh, since='v1.0.1')
    assert result.outcome is Outcome.OPENED
    assert git.commits == []
    assert git.pushes == []
    assert len(gh.created) == 1


def test_nothing_to_commit_when_version_already_bumped_and_floors_current(ws, redump):
    states = released_states(ws)
    states[REDUMP] = replace(states[REDUMP], last_tag='v1.0.0', last_tag_version=V('1.0.0'), pypi_version=V('1.0.0'))
    item = plan_release(ws, states, {REDUMP: V('1.0.1')}).item(REDUMP)  # keep the bumped 1.0.1
    item = replace(item, floor_changes=[])
    git, gh = fakes(redump)
    result = open_release_pr(item, redump, git, gh, since='v1.0.0')
    assert result.outcome is Outcome.NOTHING_TO_COMMIT
    assert 'already' in result.detail
    assert git.commits == []
    assert gh.created == []
    assert git.repos[redump.path].branch == 'master'


def test_commit_failure_restores_files_and_master(redump, item):
    before = {p: (redump.path / p).read_text() for p in (INIT, 'pyproject.toml')}
    git, gh = fakes(redump, fail_on={'commit_paths'})
    result = open_release_pr(item, redump, git, gh, since='v1.0.1')
    assert result.outcome is Outcome.ERROR
    assert 'commit_paths failed' in result.detail
    assert {p: (redump.path / p).read_text() for p in before} == before
    assert git.repos[redump.path].branch == 'master'


def test_gh_failure_after_push_is_resumable(redump, item):
    git, _ = fakes(redump)
    gh = FakeGh(fail=True)
    result = open_release_pr(item, redump, git, gh, since='v1.0.1')
    assert result.outcome is Outcome.ERROR
    assert git.pushes  # the branch is on origin, so a re-run resumes at the PR


def test_open_release_prs_runs_only_runnable_items(ws, redump):
    states = released_states(ws)
    plan = plan_release(ws, states, {'datoso': V('1.2.0'), REDUMP: V('1.0.2')})
    git = FakeGit({pkg.path: FakeRepo() for pkg in ws.packages.values()})
    gh = FakeGh()
    progress: list[tuple[str, str]] = []
    results = open_release_prs(plan, ws, states, git, gh, on_progress=lambda name, msg: progress.append((name, msg)))
    assert [(r.name, r.outcome) for r in results] == [('datoso', Outcome.OPENED)]
    assert [pr['repo'].name for pr in gh.created] == ['datoso']
    assert ('datoso', 'PR opened') in progress
