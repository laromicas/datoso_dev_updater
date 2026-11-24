"""Tests for repository state collection and assessment (written first, TDD)."""

from __future__ import annotations

from dataclasses import replace
from io import StringIO
from pathlib import Path

import pytest
from packaging.version import Version
from rich.console import Console

from datoso_dev.ports import WorkflowRun
from datoso_dev.state import PackageState, Status, collect_states, latest_release_tag
from datoso_dev.ui import status_table
from datoso_dev.workspace import discover

from .fakes import FakeGh, FakeGit, FakePyPI, FakeRepo

# --------------------------------------------------------------------------- latest_release_tag


@pytest.mark.parametrize(
    ('tags', 'expected'),
    [
        ([], None),
        (['v0.3.2', 'v1.0.0', 'v1.0.1'], ('v1.0.1', '1.0.1')),
        (['v1.10.0', 'v1.9.0', 'v1.2.0'], ('v1.10.0', '1.10.0')),  # version order, not string order
        (['1.0.0', 'v0.9.0'], ('1.0.0', '1.0.0')),  # tags without the "v" prefix count too
        (['latest', 'wip', 'v1.0.0', 'release-2'], ('v1.0.0', '1.0.0')),  # non-version tags ignored
        (['v1.1.0.dev1', 'v1.0.0'], ('v1.1.0.dev1', '1.1.0.dev1')),
    ],
)
def test_latest_release_tag(tags, expected):
    result = latest_release_tag(tags)
    assert result == (None if expected is None else (expected[0], Version(expected[1])))


# --------------------------------------------------------------------------- collect_states


@pytest.fixture
def ws(sample_workspace: Path):
    return discover(sample_workspace)


def _git_for(ws, **overrides: FakeRepo) -> FakeGit:
    repos = {
        pkg.path: FakeRepo(tags=[f'v{pkg.version}'], commits_since={f'v{pkg.version}': 0})
        for pkg in ws.packages.values()
    }
    for name, repo in overrides.items():
        repos[ws.packages[name].path] = repo
    return FakeGit(repos)


def _pypi_for(ws, **overrides: str | None) -> FakePyPI:
    versions = {pkg.dist_name: str(pkg.version) for pkg in ws.packages.values()}
    versions.update({ws.packages[n].dist_name: v for n, v in overrides.items()})
    return FakePyPI(versions)


def _state(states: list[PackageState], name: str) -> PackageState:
    return next(s for s in states if s.package.name == name)


def test_states_keep_workspace_order(ws):
    states = collect_states(ws, _git_for(ws), _pypi_for(ws))
    assert [s.package.name for s in states] == list(ws.packages)


def test_clean_released_package_is_up_to_date(ws):
    state = _state(collect_states(ws, _git_for(ws), _pypi_for(ws)), 'datoso_seed_redump')
    assert state.status is Status.UP_TO_DATE
    assert state.last_tag == 'v1.0.1'
    assert state.pypi_version == Version('1.0.1')
    assert state.issues == []


def test_commits_since_tag_means_needs_release(ws):
    git = _git_for(ws, datoso_seed_redump=FakeRepo(tags=['v1.0.0', 'v1.0.1'], commits_since={'v1.0.1': 3}))
    state = _state(collect_states(ws, git, _pypi_for(ws)), 'datoso_seed_redump')
    assert state.commits_since_tag == 3
    assert state.status is Status.NEEDS_RELEASE


def test_never_tagged_package_is_ready_for_its_first_release(ws):
    git = _git_for(ws, datoso_seed_redump=FakeRepo(tags=[], commits_since={None: 7}))
    state = _state(collect_states(ws, git, _pypi_for(ws, datoso_seed_redump=None)), 'datoso_seed_redump')
    assert state.last_tag is None
    assert state.commits_since_tag == 7
    assert state.status is Status.FIRST_RELEASE


def test_skipped_package_is_skipped_and_not_queried_on_pypi(ws):
    pypi = _pypi_for(ws)
    git = _git_for(ws, datoso_seed_mameredump=FakeRepo(tags=[], commits_since={None: 1}))
    state = _state(collect_states(ws, git, pypi), 'datoso_seed_mameredump')
    assert state.status is Status.SKIPPED
    assert 'datoso-seed-mameredump' not in pypi.queried
    assert 'not on PyPI' not in ' '.join(state.issues)


@pytest.mark.parametrize(
    ('repo', 'pypi', 'expected_issue'),
    [
        (FakeRepo(changed=['a.py', 'b.py'], tags=['v1.0.1']), '1.0.1', '2 uncommitted file(s)'),
        (FakeRepo(branch='feature/x', tags=['v1.0.1']), '1.0.1', 'on branch feature/x'),
        (FakeRepo(behind=4, tags=['v1.0.1']), '1.0.1', '4 behind origin'),
        (FakeRepo(ahead=2, tags=['v1.0.1']), '1.0.1', '2 unpushed commit(s)'),
        (FakeRepo(has_upstream=False, tags=['v1.0.1']), '1.0.1', 'no upstream'),
        (FakeRepo(tags=['v1.0.1']), None, 'v1.0.1 tagged but not on PyPI'),
        (FakeRepo(tags=['v1.0.1']), '1.0.0', 'v1.0.1 tagged but PyPI has 1.0.0'),
        (FakeRepo(tags=['v1.0.2']), '1.0.2', 'version 1.0.1 is older than tag v1.0.2'),
    ],
)
def test_issues(ws, repo, pypi, expected_issue):
    git = _git_for(ws, datoso_seed_redump=repo)
    state = _state(collect_states(ws, git, _pypi_for(ws, datoso_seed_redump=pypi)), 'datoso_seed_redump')
    assert any(expected_issue in issue for issue in state.issues), state.issues


@pytest.mark.parametrize(
    ('repo', 'pypi', 'status'),
    [
        (FakeRepo(tags=['v1.0.0'], commits_since={'v1.0.0': 2}), '1.0.0', Status.READY),  # 1.0.1 bumped, untagged
        (FakeRepo(tags=[], commits_since={None: 5}), None, Status.FIRST_RELEASE),  # never tagged nor on PyPI
    ],
)
def test_bumped_or_first_release_is_a_status_not_an_issue(ws, repo, pypi, status):
    git = _git_for(ws, datoso_seed_redump=repo)
    state = _state(collect_states(ws, git, _pypi_for(ws, datoso_seed_redump=pypi)), 'datoso_seed_redump')
    assert state.status is status
    assert state.pending
    assert state.issues == []


def test_fetch_only_when_requested(ws):
    git = _git_for(ws)
    collect_states(ws, git, _pypi_for(ws))
    assert git.called('fetch') == []
    collect_states(ws, git, _pypi_for(ws), fetch=True)
    assert sorted(git.called('fetch')) == sorted(p.path for p in ws.packages.values())


def test_git_failure_becomes_error_state_not_exception(ws):
    git = _git_for(ws, datoso_seed_redump=FakeRepo(fail_on={'tags'}))
    states = collect_states(ws, git, _pypi_for(ws))
    state = _state(states, 'datoso_seed_redump')
    assert state.status is Status.ERROR
    assert 'tags failed' in state.error
    assert _state(states, 'datoso').status is Status.UP_TO_DATE  # others unaffected


def test_pypi_failure_becomes_issue_not_error(ws):
    class BrokenPyPI(FakePyPI):
        def latest_version(self, dist_name: str) -> str | None:
            msg = 'timeout'
            raise OSError(msg)

    state = _state(collect_states(ws, _git_for(ws), BrokenPyPI()), 'datoso')
    assert state.status is Status.UP_TO_DATE
    assert 'PyPI unavailable: timeout' in state.issues


def test_offline_mode_skips_pypi_without_issues(ws):
    pypi = _pypi_for(ws)
    states = collect_states(ws, _git_for(ws), pypi, query_pypi=False)
    assert pypi.queried == []
    assert all(s.pypi_version is None and s.issues == [] for s in states)


def test_needs_release_and_issues_summary(ws):
    git = _git_for(ws, datoso_seed_redump=FakeRepo(tags=['v1.0.1'], commits_since={'v1.0.1': 2}, changed=['x']))
    states = collect_states(ws, git, _pypi_for(ws))
    assert [s.package.name for s in states if s.status is Status.NEEDS_RELEASE] == ['datoso_seed_redump']


def test_pypi_false_package_is_not_queried_and_not_flagged(ws):
    ws.packages['datoso_seed_base'] = replace(ws.packages['datoso_seed_base'], release=True)  # GitHub-only seed
    pypi = _pypi_for(ws)
    git = _git_for(ws, datoso_seed_base=FakeRepo(tags=['v1.0.0'], commits_since={'v1.0.0': 3}))
    state = _state(collect_states(ws, git, pypi), 'datoso_seed_base')
    assert 'datoso-seed-base' not in pypi.queried
    assert state.pypi_version is None
    assert state.issues == []
    assert state.status is Status.NEEDS_RELEASE  # still released (GitHub only)


def test_pypi_false_with_publish_workflow_is_flagged(tmp_path: Path):
    from .conftest import write_gitmodules, write_package  # noqa: PLC0415

    write_package(tmp_path, 'datoso', '1.0.0', pypi=False, publish_workflow=True)
    write_gitmodules(tmp_path, ['datoso'])
    ws = discover(tmp_path)
    git = FakeGit({ws.packages['datoso'].path: FakeRepo(tags=['v1.0.0'])})
    state = collect_states(ws, git, FakePyPI())[0]
    assert any('pypi = false but a workflow publishes to PyPI on release' in i for i in state.issues)


def test_template_is_never_released(ws):
    git = _git_for(ws, datoso_seed_base=FakeRepo(tags=[], commits_since={None: 36}))
    state = _state(collect_states(ws, git, _pypi_for(ws)), 'datoso_seed_base')
    assert state.status is Status.TEMPLATE
    assert not state.pending
    assert state.issues == []


# --------------------------------------------------------------------------- tag newer than PyPI


class LaggingPyPI(FakePyPI):
    """The "latest" endpoint is cached (1.0.0), but the exact-version endpoint may already know 1.0.1."""

    def __init__(self, versions, *, exact: bool) -> None:
        super().__init__(versions)
        self.exact = exact

    def has_version(self, dist_name, version):
        self.polls.append((dist_name, version))
        return self.exact


def lagging(ws, *, exact: bool = False, gh=None) -> PackageState:
    git = _git_for(ws, datoso_seed_redump=FakeRepo(tags=['v1.0.1']))
    pypi = LaggingPyPI(_pypi_for(ws, datoso_seed_redump='1.0.0').versions, exact=exact)
    return _state(collect_states(ws, git, pypi, gh=gh or FakeGh()), 'datoso_seed_redump')


def test_cached_latest_but_exact_version_is_there(ws):
    state = lagging(ws, exact=True)
    assert state.pypi_version == Version('1.0.1')
    assert state.issues == []
    assert state.notes == []


@pytest.mark.parametrize(
    ('run', 'note'),
    [
        (('in_progress', ''), 'v1.0.1 is being published to PyPI (in progress)'),
        (('queued', ''), 'v1.0.1 is being published to PyPI (queued)'),
        (('completed', 'success'), 'v1.0.1 published; PyPI not listing it yet'),
    ],
)
def test_publish_running_or_done_is_a_note_not_an_issue(ws, run, note):
    gh = FakeGh()
    gh.runs[(ws.packages['datoso_seed_redump'].path, 'v1.0.1')] = WorkflowRun(*run, url='https://x/runs/1')
    state = lagging(ws, gh=gh)
    assert state.issues == []
    assert state.notes == [note]
    assert state.status is Status.UP_TO_DATE


def test_failed_publish_run_is_an_issue_with_its_link(ws):
    gh = FakeGh()
    gh.runs[(ws.packages['datoso_seed_redump'].path, 'v1.0.1')] = WorkflowRun(
        'completed', 'failure', 'https://x/runs/2'
    )
    state = lagging(ws, gh=gh)
    assert state.issues == ['v1.0.1 publish workflow failure: https://x/runs/2']
    assert state.notes == []


def test_no_run_or_gh_failure_keeps_the_old_warning(ws):
    failing = FakeGh()
    failing.fail_runs = True
    for state in (lagging(ws), lagging(ws, gh=failing)):
        assert state.issues == ['v1.0.1 tagged but PyPI has 1.0.0 (publish failed?)']


def test_notes_are_shown_dimmed_after_the_issues(ws):

    state = replace(
        lagging(ws, exact=True), issues=['1 behind origin'], notes=['v1.0.1 published; PyPI not listing it yet']
    )
    out = StringIO()
    Console(file=out, width=300).print(status_table([state]))
    assert '1 behind origin; v1.0.1 published; PyPI not listing it yet' in out.getvalue()
