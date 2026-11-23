"""Tests for the full release pipeline (TDD): PR → CI → merge → publish → PyPI, step by step, then core extras floors
and workspace pointers. Everything is resumable from what GitHub/PyPI already show.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from packaging.version import Version

from datoso_dev.pipeline import Waits, commit_pointers, release_batch, update_core_extras
from datoso_dev.planner import plan_release
from datoso_dev.ports import Checks
from datoso_dev.release import Outcome, probe_releases
from datoso_dev.state import PackageState
from datoso_dev.workspace import Workspace, discover

from .fakes import FakeGh, FakeGit, FakePyPI, FakeRepo

V = Version
REDUMP = 'datoso_seed_redump'


@pytest.fixture
def ws(sample_workspace: Path):
    return discover(sample_workspace)


@pytest.fixture
def states(ws):
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
def git(ws):
    repos = {pkg.path: FakeRepo() for pkg in ws.packages.values()}
    repos[ws.root] = FakeRepo()
    return FakeGit(repos)


@pytest.fixture
def gh():
    return FakeGh()


@pytest.fixture
def pypi():
    """PyPI lists a version as soon as it is polled for (the publish workflow is instant)."""

    class InstantPyPI(FakePyPI):
        def has_version(self, dist_name, version):
            self.polls.append((dist_name, version))
            return True

    return InstantPyPI()


@dataclass
class Env:
    ws: Workspace
    states: dict
    git: FakeGit
    gh: FakeGh
    pypi: FakePyPI


@pytest.fixture
def env(ws, states, git, gh, pypi):
    return Env(ws, states, git, gh, pypi)


def no_wait() -> Waits:
    clock = itertools.count(0, 15)
    return Waits(sleep=lambda _s: None, clock=lambda: next(clock))


def run(env: Env, selection, *, pypi: FakePyPI | None = None):
    plan = plan_release(env.ws, env.states, selection)
    probes = probe_releases(plan, env.ws, env.git, env.gh)
    pypi = pypi or env.pypi
    return plan, release_batch(plan, env.ws, env.states, env.git, env.gh, pypi, probes=probes, waits=no_wait())


def outcomes(batch):
    return {r.name: r.outcome for r in batch.results}


# --------------------------------------------------------------------------- one package, end to end


def test_full_release_of_one_seed(env):
    _, batch = run(env, {REDUMP: V('1.0.2')})
    assert outcomes(batch) == {REDUMP: Outcome.PUBLISHED}
    [pr] = env.gh.created
    assert env.gh.merged == [pr['url']]
    assert [(r['tag'], r['target']) for r in env.gh.releases_created] == [('v1.0.2', 'sha-of-1')]
    assert ('datoso-seed-redump', '1.0.2') in env.pypi.polls
    assert env.ws.packages[REDUMP].path in env.git.called('pull_rebase')  # local master follows the merge
    assert batch.published == {REDUMP: V('1.0.2')}


def test_waits_for_pending_checks(env):
    url = 'https://github.com/owner/datoso_seed_redump/pull/1'
    env.gh.checks[url] = [(Checks.NONE, ''), (Checks.PENDING, ''), (Checks.PENDING, ''), (Checks.PASSED, '')]
    _, batch = run(env, {REDUMP: V('1.0.2')})
    assert outcomes(batch) == {REDUMP: Outcome.PUBLISHED}
    assert env.gh.checks[url] == [(Checks.PASSED, '')]  # polled through every state


@pytest.mark.parametrize(
    ('states_seq', 'detail'),
    [
        ([(Checks.FAILED, 'check: https://github.com/o/r/runs/1')], 'check: https://github.com/o/r/runs/1'),
        ([(Checks.NONE, '')], 'no CI checks reported'),
        ([(Checks.PENDING, '')], 'still running after 30 min'),
    ],
)
def test_ci_not_green_leaves_the_pr_open(env, states_seq, detail):
    env.gh.checks['https://github.com/owner/datoso_seed_redump/pull/1'] = states_seq
    _, batch = run(env, {REDUMP: V('1.0.2')})
    [result] = batch.results
    assert result.outcome is Outcome.CHECKS_FAILED
    assert detail in result.detail
    assert result.pr_url == 'https://github.com/owner/datoso_seed_redump/pull/1'
    assert env.gh.merged == []
    assert env.gh.releases_created == []


def test_merge_refused(env):
    env.gh.fail_merge = True
    _, batch = run(env, {REDUMP: V('1.0.2')})
    [result] = batch.results
    assert result.outcome is Outcome.ERROR
    assert 'policy prohibits the merge' in result.detail
    assert env.gh.releases_created == []


# --------------------------------------------------------------------------- resume


def test_resume_merges_an_open_pr(env):
    url = 'https://github.com/owner/datoso_seed_redump/pull/7'
    env.gh.open_prs[(env.ws.packages[REDUMP].path, 'release/v1.0.2')] = url
    _, batch = run(env, {REDUMP: V('1.0.2')})
    assert outcomes(batch) == {REDUMP: Outcome.PUBLISHED}
    assert env.gh.created == []
    assert env.gh.merged == [url]


def test_resume_publishes_a_merged_pr(env):
    env.gh.merged_prs[(env.ws.packages[REDUMP].path, 'release/v1.0.2')] = (
        'https://github.com/owner/datoso_seed_redump/pull/7'
    )
    _, batch = run(env, {REDUMP: V('1.0.2')})
    assert outcomes(batch) == {REDUMP: Outcome.PUBLISHED}
    assert env.gh.merged == []
    assert [r['target'] for r in env.gh.releases_created] == ['sha-of-7']


def test_resume_existing_release_only_checks_pypi(env):
    env.gh.releases[(env.ws.packages[REDUMP].path, 'v1.0.2')] = (
        'https://github.com/owner/datoso_seed_redump/releases/v1.0.2'
    )
    _, batch = run(env, {REDUMP: V('1.0.2')})
    assert outcomes(batch) == {REDUMP: Outcome.ALREADY_RELEASED}
    assert env.gh.releases_created == []
    assert batch.published == {REDUMP: V('1.0.2')}


# --------------------------------------------------------------------------- steps


def test_later_steps_run_once_earlier_ones_are_on_pypi(env):
    plan, batch = run(env, {'datoso': V('1.2.0'), REDUMP: V('1.0.2')})
    assert [len(step) for step in plan.steps] == [1, 1]
    assert outcomes(batch) == {'datoso': Outcome.PUBLISHED, REDUMP: Outcome.PUBLISHED}
    assert [pr['repo'].name for pr in env.gh.created] == ['datoso', REDUMP]
    assert [r['tag'] for r in env.gh.releases_created] == ['v1.2.0', 'v1.0.2']
    # the seed's release commit raises its floor to the core version just published
    assert '`datoso>=1.1.0` → `datoso>=1.2.0`' in str(env.gh.created[1]['body'])


def test_a_failed_step_blocks_its_dependents(env):
    env.gh.checks['https://github.com/owner/datoso/pull/1'] = [(Checks.FAILED, 'tests: https://x')]
    _, batch = run(env, {'datoso': V('1.2.0'), REDUMP: V('1.0.2')})
    assert outcomes(batch) == {'datoso': Outcome.CHECKS_FAILED, REDUMP: Outcome.BLOCKED}
    assert 'waits for datoso' in next(r for r in batch.results if r.name == REDUMP).detail
    assert [pr['repo'].name for pr in env.gh.created] == ['datoso']


def test_pypi_pending_blocks_dependents(env):
    _, batch = run(env, {'datoso': V('1.2.0'), REDUMP: V('1.0.2')}, pypi=FakePyPI())
    assert outcomes(batch) == {'datoso': Outcome.PYPI_PENDING, REDUMP: Outcome.BLOCKED}


# --------------------------------------------------------------------------- core extras floors


def test_core_extras_floors_follow_the_published_seeds(ws, git):
    core = ws.packages['datoso']
    result = update_core_extras(ws, git, {REDUMP: V('1.0.2')})
    assert result.outcome is Outcome.UPDATED
    assert '"datoso-seed-redump>=1.0.2"' in (core.path / 'pyproject.toml').read_text()
    assert git.commits == [(core.path, ['pyproject.toml'], 'Update extras floors: datoso-seed-redump>=1.0.2')]
    assert git.pushes == [(core.path, 'master', False)]
    assert core.path in git.called('pull_rebase')


def test_core_extras_message_names_each_floor_once(ws, git):
    core = ws.packages['datoso']
    text = (core.path / 'pyproject.toml').read_text()
    (core.path / 'pyproject.toml').write_text(
        text.replace(
            '[project.optional-dependencies]',
            '[project.optional-dependencies]\nall = [ "datoso-seed-redump>=1.0.1" ]',
            1,
        )
    )
    ws = discover(ws.root)
    update_core_extras(ws, git, {REDUMP: V('1.0.2')})
    assert git.commits[0][2] == 'Update extras floors: datoso-seed-redump>=1.0.2'
    assert (core.path / 'pyproject.toml').read_text().count('datoso-seed-redump>=1.0.2') == 2


def test_core_extras_nothing_to_do(ws, git):
    assert update_core_extras(ws, git, {'datoso_seed_enhanced': V('1.1.1')}) is None  # not a core extra
    assert git.commits == []


def test_core_extras_skipped_when_core_is_dirty(ws, git):
    git.repos[ws.packages['datoso'].path].changed = ['wip.py']
    result = update_core_extras(ws, git, {REDUMP: V('1.0.2')})
    assert result.outcome is Outcome.SKIPPED
    assert 'datoso-seed-redump>=1.0.2' in result.detail  # tells what to do by hand
    assert git.commits == []


# --------------------------------------------------------------------------- workspace pointers


def test_pointers_commit_only_the_released_submodules(ws, git):
    git.repos[ws.root].changed = ['datoso', REDUMP, 'datoso_seed_enhanced', 'notes.txt']
    result = commit_pointers(ws, git, {REDUMP: V('1.0.2')}, extras=True)
    assert result.outcome is Outcome.UPDATED
    assert git.commits == [(ws.root, ['datoso', REDUMP], 'Release: datoso_seed_redump 1.0.2; core extras floors')]
    assert git.pushes == [(ws.root, 'master', False)]


def test_pointers_nothing_changed(ws, git):
    assert commit_pointers(ws, git, {REDUMP: V('1.0.2')}, extras=False) is None


def test_pointers_skipped_off_master(ws, git):
    git.repos[ws.root].changed = [REDUMP]
    git.repos[ws.root].branch = 'docs'
    result = commit_pointers(ws, git, {REDUMP: V('1.0.2')}, extras=False)
    assert result.outcome is Outcome.SKIPPED
    assert git.commits == []


def test_no_wait_still_waits_for_packages_others_depend_on(env):
    plan = plan_release(env.ws, env.states, {'datoso': V('1.2.0'), REDUMP: V('1.0.2')})
    probes = probe_releases(plan, env.ws, env.git, env.gh)
    waits = replace(no_wait(), wait_pypi=False)
    release_batch(plan, env.ws, env.states, env.git, env.gh, env.pypi, probes=probes, waits=waits)
    assert ('datoso', '1.2.0') in env.pypi.polls  # the seed's CI needs it on PyPI
    assert ('datoso-seed-redump', '1.0.2') not in env.pypi.polls  # nothing depends on the seed
