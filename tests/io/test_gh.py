"""Contract tests for the gh adapter: exact argv, output parsing, error mapping (no network)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from datoso_dev.io.gh import Gh
from datoso_dev.ports import Checks, GhError


class Runner:
    def __init__(self, stdout: str = '', returncode: int = 0, stderr: str = '', exc: Exception | None = None) -> None:
        self.stdout, self.returncode, self.stderr, self.exc = stdout, returncode, stderr, exc
        self.calls: list[tuple[list[str], Path]] = []

    def __call__(self, argv: list[str], *, cwd: Path, **_: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, cwd))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


def test_pr_for_branch_argv_and_url(tmp_path: Path):
    run = Runner(stdout='https://github.com/o/r/pull/3\n')
    assert Gh(runner=run).pr_for_branch(tmp_path, 'datoso-dev/ci') == 'https://github.com/o/r/pull/3'
    argv, cwd = run.calls[0]
    assert argv == [
        'gh', 'pr', 'list', '--head', 'datoso-dev/ci', '--state', 'open', '--json', 'url', '--jq', '.[0].url // ""',
    ]  # fmt: skip
    assert cwd == tmp_path


def test_pr_for_branch_none_when_empty(tmp_path: Path):
    assert Gh(runner=Runner(stdout='\n')).pr_for_branch(tmp_path, 'x') is None


def test_pr_for_branch_merged_state(tmp_path: Path):
    run = Runner(stdout='https://github.com/o/r/pull/4\n')
    assert Gh(runner=run).pr_for_branch(tmp_path, 'release/v1.1.0', state='merged').endswith('/pull/4')
    assert run.calls[0][0][4:7] == ['release/v1.1.0', '--state', 'merged']


def test_pr_create_argv_and_url(tmp_path: Path):
    run = Runner(stdout='Creating pull request…\nhttps://github.com/o/r/pull/4\n')
    url = Gh(runner=run).pr_create(tmp_path, base='master', head='datoso-dev/ci', title='T', body='B')
    assert url == 'https://github.com/o/r/pull/4'
    assert run.calls[0][0] == [
        'gh', 'pr', 'create', '--base', 'master', '--head', 'datoso-dev/ci', '--title', 'T', '--body', 'B',
    ]  # fmt: skip


def test_failure_maps_to_gh_error(tmp_path: Path):
    with pytest.raises(GhError, match='not logged in'):
        Gh(runner=Runner(returncode=1, stderr='not logged in')).pr_for_branch(tmp_path, 'x')


def test_missing_executable_maps_to_gh_error(tmp_path: Path):
    with pytest.raises(GhError, match='No such file'):
        Gh(runner=Runner(exc=FileNotFoundError('No such file: gh'))).pr_for_branch(tmp_path, 'x')


def test_pr_merge_commit(tmp_path: Path):
    run = Runner(stdout='e60c047abc\n')
    assert Gh(runner=run).pr_merge_commit(tmp_path, 'https://github.com/o/r/pull/4') == 'e60c047abc'
    assert run.calls[0][0] == [
        'gh', 'pr', 'view', 'https://github.com/o/r/pull/4', '--json', 'mergeCommit', '--jq', '.mergeCommit.oid',
    ]  # fmt: skip


def test_release_url_found_and_not_found(tmp_path: Path):
    run = Runner(stdout='https://github.com/o/r/releases/tag/v1.1.0\n')
    assert Gh(runner=run).release_url(tmp_path, 'v1.1.0').endswith('/v1.1.0')
    assert run.calls[0][0] == ['gh', 'release', 'view', 'v1.1.0', '--json', 'url', '--jq', '.url']
    assert Gh(runner=Runner(returncode=1, stderr='release not found')).release_url(tmp_path, 'v9') is None
    with pytest.raises(GhError, match='HTTP 502'):
        Gh(runner=Runner(returncode=1, stderr='HTTP 502')).release_url(tmp_path, 'v9')


def test_release_create_argv(tmp_path: Path):
    run = Runner(stdout='https://github.com/o/r/releases/tag/v1.1.0\n')
    url = Gh(runner=run).release_create(tmp_path, tag='v1.1.0', target='abc', title='v1.1.0', notes_start_tag='v1.0.0')
    assert url.endswith('/v1.1.0')
    assert run.calls[0][0] == [
        'gh', 'release', 'create', 'v1.1.0', '--target', 'abc', '--title', 'v1.1.0', '--generate-notes',
        '--notes-start-tag', 'v1.0.0',
    ]  # fmt: skip
    Gh(runner=run).release_create(tmp_path, tag='v2.0.0rc1', target='abc', title='v2.0.0rc1', prerelease=True)
    assert run.calls[1][0][-2:] == ['--generate-notes', '--prerelease']


CHECKS_OUT = 'GitGuardian Security Checks\tpass\t1s\thttps://x\t\ncheck\tfail\t10s\thttps://github.com/o/r/runs/1\t\n'


@pytest.mark.parametrize(
    ('returncode', 'stdout', 'stderr', 'state'),
    [
        (0, 'check\tpass\t10s\thttps://x\t\n', '', Checks.PASSED),
        (8, 'check\tpending\t0\thttps://x\t\n', '', Checks.PENDING),
        (1, CHECKS_OUT, '', Checks.FAILED),
        (1, '', "no checks reported on the 'release/v1.0.2' branch", Checks.NONE),
    ],
)
def test_pr_checks_states(tmp_path: Path, returncode, stdout, stderr, state):
    run = Runner(stdout=stdout, returncode=returncode, stderr=stderr)
    result, detail = Gh(runner=run).pr_checks(tmp_path, 'https://github.com/o/r/pull/4')
    assert result is state
    assert run.calls[0][0] == ['gh', 'pr', 'checks', 'https://github.com/o/r/pull/4']
    if state is Checks.FAILED:
        assert detail == 'check: https://github.com/o/r/runs/1'


def test_pr_checks_other_errors_raise(tmp_path: Path):
    with pytest.raises(GhError, match='HTTP 401'):
        Gh(runner=Runner(returncode=1, stderr='HTTP 401')).pr_checks(tmp_path, 'u')


def test_pr_merge_argv(tmp_path: Path):
    run = Runner()
    Gh(runner=run).pr_merge(tmp_path, 'https://github.com/o/r/pull/4')
    assert run.calls[0][0] == ['gh', 'pr', 'merge', 'https://github.com/o/r/pull/4', '--squash', '--delete-branch']


def test_release_notes_and_edit(tmp_path: Path):
    run = Runner(stdout='## What changed\n* x\n')
    assert Gh(runner=run).release_notes(tmp_path, 'v1.1.0') == '## What changed\n* x'
    assert run.calls[0][0] == ['gh', 'release', 'view', 'v1.1.0', '--json', 'body', '--jq', '.body']
    notes = tmp_path / 'notes.md'
    Gh(runner=run).release_edit_notes(tmp_path, 'v1.1.0', notes)
    assert run.calls[1][0] == ['gh', 'release', 'edit', 'v1.1.0', '--notes-file', str(notes)]
