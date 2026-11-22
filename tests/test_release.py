"""Tests for the release tasks up to "PR opened" (TDD): idempotent, resumable, never leave master dirty."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from packaging.version import Version

from datoso_dev.planner import plan_release
from datoso_dev.release import (
    Action,
    Outcome,
    open_release_pr,
    probe_release,
    publish_release,
    run_releases,
    wait_for_pypi,
)
from datoso_dev.state import PackageState
from datoso_dev.workspace import discover

from .fakes import FakeGh, FakeGit, FakePyPI, FakeRepo

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


def test_merged_pr_means_tag_it_not_a_new_pr(redump, item):
    """The release PR was merged but not tagged yet: re-running must not open a duplicate from the stale branch."""
    pushed = (redump.path / INIT).read_text().replace("'1.0.1'", "'1.0.2'")
    git, gh = fakes(redump, files={('origin/release/v1.0.2', INIT): pushed})
    gh.merged_prs[(redump.path, 'release/v1.0.2')] = 'https://github.com/owner/datoso_seed_redump/pull/4'
    gh.open_prs[(redump.path, 'release/v1.0.2')] = 'https://github.com/owner/datoso_seed_redump/pull/5'  # duplicate
    result = open_release_pr(item, redump, git, gh, since='v1.0.1')
    assert result.outcome is Outcome.MERGED
    assert result.pr_url.endswith('/pull/4')
    assert 'tag v1.0.2' in result.detail
    assert gh.created == []
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


def test_run_releases_runs_only_runnable_items(ws, redump):
    states = released_states(ws)
    plan = plan_release(ws, states, {'datoso': V('1.2.0'), REDUMP: V('1.0.2')})
    git = FakeGit({pkg.path: FakeRepo() for pkg in ws.packages.values()})
    gh = FakeGh()
    probes = {item.name: probe_release(item, ws.packages[item.name], git, gh) for item in plan.runnable_now}
    progress: list[tuple[str, str]] = []
    results = run_releases(
        plan, probes, ws, states, git, gh, FakePyPI(), on_progress=lambda name, msg: progress.append((name, msg))
    )
    assert [(r.name, r.outcome) for r in results] == [('datoso', Outcome.OPENED)]
    assert [pr['repo'].name for pr in gh.created] == ['datoso']
    assert ('datoso', 'PR opened') in progress


# --------------------------------------------------------------------------- probe: what is left to do


BRANCH = 'release/v1.0.2'
MERGED_URL = 'https://github.com/owner/datoso_seed_redump/pull/4'


def test_probe_no_pr_means_open_one(redump, item):
    git, gh = fakes(redump)
    assert probe_release(item, redump, git, gh).action is Action.OPEN_PR
    assert git.called('fetch')


def test_probe_open_pr_means_merge_it_first(redump, item):
    git, gh = fakes(redump)
    gh.open_prs[(redump.path, BRANCH)] = 'https://github.com/owner/datoso_seed_redump/pull/9'
    probe = probe_release(item, redump, git, gh)
    assert (probe.action, probe.pr_url) == (Action.WAIT_MERGE, 'https://github.com/owner/datoso_seed_redump/pull/9')


def test_probe_merged_pr_means_publish(redump, item):
    git, gh = fakes(redump)
    gh.merged_prs[(redump.path, BRANCH)] = MERGED_URL
    gh.open_prs[(redump.path, BRANCH)] = 'https://github.com/owner/datoso_seed_redump/pull/5'  # a stale duplicate
    probe = probe_release(item, redump, git, gh)
    assert (probe.action, probe.pr_url) == (Action.PUBLISH, MERGED_URL)


def test_probe_existing_release_means_done(redump, item):
    git, gh = fakes(redump)
    gh.releases[(redump.path, 'v1.0.2')] = 'https://github.com/owner/datoso_seed_redump/releases/tag/v1.0.2'
    assert probe_release(item, redump, git, gh).action is Action.RELEASED


def test_probe_error(redump, item):
    git, gh = fakes(redump, fail_on={'fetch'})
    probe = probe_release(item, redump, git, gh)
    assert probe.action is Action.ERROR
    assert 'fetch failed' in probe.detail


# --------------------------------------------------------------------------- publish: tag + GitHub Release + PyPI


def no_sleep(_seconds: float) -> None:
    pass


def test_publish_tags_the_merge_commit_and_waits_for_pypi(redump, item):
    _, gh = fakes(redump)
    pypi = FakePyPI()
    pypi.appears_after[('datoso-seed-redump', '1.0.2')] = 2
    result = publish_release(item, redump, gh, pypi, pr_url=MERGED_URL, since='v1.0.1', sleep=no_sleep)
    assert result.outcome is Outcome.PUBLISHED
    assert result.pr_url == 'https://github.com/owner/datoso_seed_redump/releases/tag/v1.0.2'
    [created] = gh.releases_created
    assert created == {
        'repo': redump.path, 'tag': 'v1.0.2', 'target': 'sha-of-4', 'title': 'v1.0.2', 'notes_start_tag': 'v1.0.1',
        'prerelease': False,
    }  # fmt: skip
    assert len(pypi.polls) == 3
    assert 'on PyPI' in result.detail


def test_publish_pypi_timeout_is_reported_not_failed(redump, item):
    _, gh = fakes(redump)
    clock = iter(range(0, 10_000, 30))
    result = publish_release(
        item, redump, gh, FakePyPI(), pr_url=MERGED_URL, since='v1.0.1', sleep=no_sleep,
        clock=lambda: next(clock), timeout=120,
    )  # fmt: skip
    assert result.outcome is Outcome.PYPI_PENDING
    assert 'publish workflow' in result.detail


def test_publish_without_wait_and_prerelease(ws, redump):
    _, gh = fakes(redump)
    item = plan_release(ws, released_states(ws), {REDUMP: V('1.1.0rc1')}).item(REDUMP)
    pypi = FakePyPI()
    result = publish_release(item, redump, gh, pypi, pr_url=MERGED_URL, since='v1.0.1', wait=False)
    assert result.outcome is Outcome.PUBLISHED
    assert gh.releases_created[0]['prerelease'] is True
    assert pypi.polls == []


def test_publish_github_only_package_does_not_wait(ws, redump):
    _, gh = fakes(redump)
    pkg = replace(redump, pypi=False)
    pypi = FakePyPI()
    assert publish_release(item_for(ws), pkg, gh, pypi, pr_url=MERGED_URL, since=None).outcome is Outcome.PUBLISHED
    assert pypi.polls == []
    assert gh.releases_created[0]['notes_start_tag'] is None


def item_for(ws):
    return plan_release(ws, released_states(ws), {REDUMP: V('1.0.2')}).item(REDUMP)


def test_publish_error(redump, item):
    gh = FakeGh(fail=True)
    result = publish_release(item, redump, gh, FakePyPI(), pr_url=MERGED_URL, since='v1.0.1')
    assert result.outcome is Outcome.ERROR


def test_wait_for_pypi_handles_network_errors():
    class Flaky(FakePyPI):
        def has_version(self, dist_name, version):
            self.polls.append((dist_name, version))
            if len(self.polls) == 1:
                msg = 'timeout'
                raise OSError(msg)
            return True

    assert wait_for_pypi(Flaky(), 'x', '1.0.0', sleep=no_sleep) is True


def test_run_releases_dispatches_on_the_probe(ws, redump, item):
    states = released_states(ws)
    plan = plan_release(ws, states, {REDUMP: V('1.0.2'), 'datoso_seed_enhanced': V('1.1.1')})
    git = FakeGit({pkg.path: FakeRepo() for pkg in ws.packages.values()})
    gh = FakeGh()
    probes = {
        REDUMP: probe_release(item, redump, git, gh),
        'datoso_seed_enhanced': probe_release(
            plan.item('datoso_seed_enhanced'), ws.packages['datoso_seed_enhanced'], git, gh
        ),
    }
    gh.merged_prs[(redump.path, BRANCH)] = MERGED_URL
    probes[REDUMP] = probe_release(item, redump, git, gh)
    results = run_releases(plan, probes, ws, states, git, gh, FakePyPI(), wait=False)
    assert {r.name: r.outcome for r in results} == {REDUMP: Outcome.PUBLISHED, 'datoso_seed_enhanced': Outcome.OPENED}
