"""Tests for the managed seed CI workflow (written first, TDD)."""

from __future__ import annotations

from pathlib import Path

import pytest

from datoso_dev.ci import (
    CI_BRANCH,
    WORKFLOW_PATH,
    CiOutcome,
    CiStatus,
    ci_status,
    ci_sync,
    ci_targets,
    render_ci,
)
from datoso_dev.workspace import WorkspaceError, discover

from .fakes import FakeGh, FakeGit, FakeRepo

# --------------------------------------------------------------------------- render_ci


def test_render_seed_imports_package_and_runs_doctor(sample_workspace: Path):
    ws = discover(sample_workspace)
    text = render_ci(ws.packages['datoso_seed_redump'])
    assert 'import datoso_seed_redump as package' in text
    assert 'run: datoso doctor redump' in text
    assert text.startswith('# Managed by datoso-dev')
    assert '@' not in text.replace('actions/checkout@v4', '').replace('actions/setup-python@v5', '')


def test_render_plugin_has_no_doctor_step(sample_workspace: Path):
    ws = discover(sample_workspace)
    text = render_ci(ws.packages['datoso_plugin_internetarchive'])
    assert 'import datoso_plugin_internetarchive as package' in text
    assert 'doctor' not in text


def test_render_is_valid_yaml_shape(sample_workspace: Path):
    """Steps stay inside the job: every step line keeps the template's 6-space indent."""
    ws = discover(sample_workspace)
    text = render_ci(ws.packages['datoso_seed_redump'])
    step_lines = [line for line in text.splitlines() if line.lstrip().startswith('- ')]
    assert step_lines
    assert all(line.startswith('      - ') for line in step_lines)
    assert text.endswith('\n')


def test_render_custom_template(sample_workspace: Path):
    ws = discover(sample_workspace)
    text = render_ci(ws.packages['datoso_seed_redump'], template='pkg=@PACKAGE@\n@SEED_STEPS@end\n')
    assert text.startswith('pkg=datoso_seed_redump\n      - name: datoso doctor\n')
    assert text.endswith('end\n')


# --------------------------------------------------------------------------- ci_targets


def test_targets_exclude_core_and_skipped(sample_workspace: Path):
    ws = discover(sample_workspace)
    names = [p.name for p in ci_targets(ws)]
    assert names == ['datoso_plugin_internetarchive', 'datoso_seed_redump', 'datoso_seed_enhanced', 'datoso_seed_base']


def test_targets_include_skipped(sample_workspace: Path):
    ws = discover(sample_workspace)
    assert 'datoso_seed_mameredump' in [p.name for p in ci_targets(ws, include_skipped=True)]


def test_targets_only_selected(sample_workspace: Path):
    ws = discover(sample_workspace)
    assert [p.name for p in ci_targets(ws, only=['datoso_seed_redump', 'base'])] == [
        'datoso_seed_redump',
        'datoso_seed_base',
    ]


@pytest.mark.parametrize('name', ['datoso', 'nope'])
def test_targets_only_rejects_unknown_or_core(sample_workspace: Path, name: str):
    ws = discover(sample_workspace)
    with pytest.raises(WorkspaceError, match=name):
        ci_targets(ws, only=[name])


# --------------------------------------------------------------------------- ci_status


def test_status_missing_outdated_up_to_date(sample_workspace: Path):
    pkg = discover(sample_workspace).packages['datoso_seed_redump']
    assert ci_status(pkg) is CiStatus.MISSING
    workflow = pkg.path / WORKFLOW_PATH
    workflow.parent.mkdir(parents=True)
    workflow.write_text('old\n')
    assert ci_status(pkg) is CiStatus.OUTDATED
    workflow.write_text(render_ci(pkg))
    assert ci_status(pkg) is CiStatus.UP_TO_DATE


# --------------------------------------------------------------------------- ci_sync


@pytest.fixture
def redump(sample_workspace: Path):
    return discover(sample_workspace).packages['datoso_seed_redump']


def test_sync_up_to_date_does_nothing(redump):
    (redump.path / WORKFLOW_PATH).parent.mkdir(parents=True)
    (redump.path / WORKFLOW_PATH).write_text(render_ci(redump))
    git, gh = FakeGit({redump.path: FakeRepo()}), FakeGh()
    result = ci_sync(redump, git, gh)
    assert result.outcome is CiOutcome.UP_TO_DATE
    assert git.called('create_branch') == []
    assert git.commits == []
    assert gh.created == []


def test_sync_missing_opens_pr_and_returns_to_master(redump):
    git, gh = FakeGit({redump.path: FakeRepo()}), FakeGh()
    result = ci_sync(redump, git, gh)
    assert result.outcome is CiOutcome.OPENED
    assert result.pr_url == 'https://github.com/owner/datoso_seed_redump/pull/1'
    assert (redump.path / WORKFLOW_PATH).read_text() == render_ci(redump)
    assert git.commits == [(redump.path, [str(WORKFLOW_PATH)], 'CI: add datoso-dev managed workflow')]
    assert git.pushes == [(redump.path, CI_BRANCH, True)]
    assert git.repos[redump.path].branch == 'master'
    [pr] = gh.created
    assert pr['base'] == 'master'
    assert pr['head'] == CI_BRANCH
    assert pr['title'] == 'CI: add datoso-dev managed workflow'


def test_sync_outdated_with_open_pr_updates_branch_only(redump):
    (redump.path / WORKFLOW_PATH).parent.mkdir(parents=True)
    (redump.path / WORKFLOW_PATH).write_text('old\n')
    url = 'https://github.com/owner/datoso_seed_redump/pull/7'
    git, gh = FakeGit({redump.path: FakeRepo()}), FakeGh({(redump.path, CI_BRANCH): url})
    result = ci_sync(redump, git, gh)
    assert result.outcome is CiOutcome.UPDATED
    assert result.pr_url == url
    assert git.commits[0][2] == 'CI: update datoso-dev managed workflow'
    assert gh.created == []


@pytest.mark.parametrize(
    ('repo', 'detail'),
    [
        (FakeRepo(changed=['x.py']), 'uncommitted'),
        (FakeRepo(branch='feature', branches={'master', 'feature'}), 'on feature'),
        (FakeRepo(behind=2), 'behind'),
        (FakeRepo(ahead=1), 'unpushed'),
        (FakeRepo(has_upstream=False), 'no upstream'),
    ],
)
def test_sync_skips_unsafe_repos(redump, repo: FakeRepo, detail: str):
    git, gh = FakeGit({redump.path: repo}), FakeGh()
    result = ci_sync(redump, git, gh)
    assert result.outcome is CiOutcome.SKIPPED
    assert detail in result.detail
    assert git.commits == []
    assert not (redump.path / WORKFLOW_PATH).exists()


def test_sync_dry_run_changes_nothing(redump):
    git, gh = FakeGit({redump.path: FakeRepo()}), FakeGh()
    result = ci_sync(redump, git, gh, dry_run=True)
    assert result.outcome is CiOutcome.OPENED
    assert result.dry_run is True
    assert not (redump.path / WORKFLOW_PATH).exists()
    assert git.called('create_branch') == []
    assert gh.created == []


def test_sync_git_failure_reports_error_and_returns_to_master(redump):
    git, gh = FakeGit({redump.path: FakeRepo(fail_on={'push'})}), FakeGh()
    result = ci_sync(redump, git, gh)
    assert result.outcome is CiOutcome.ERROR
    assert 'push failed' in result.detail
    assert git.repos[redump.path].branch == 'master'


def test_sync_gh_failure_reports_error(redump):
    git, gh = FakeGit({redump.path: FakeRepo()}), FakeGh(fail=True)
    result = ci_sync(redump, git, gh)
    assert result.outcome is CiOutcome.ERROR
    assert 'gh pr create failed' in result.detail


def test_sync_commit_failure_leaves_no_stray_workflow(redump):
    git, gh = FakeGit({redump.path: FakeRepo(fail_on={'commit_paths'})}), FakeGh()
    result = ci_sync(redump, git, gh)
    assert result.outcome is CiOutcome.ERROR
    assert not (redump.path / WORKFLOW_PATH).exists()
    assert git.repos[redump.path].branch == 'master'


def test_sync_commit_failure_restores_previous_workflow(redump):
    (redump.path / WORKFLOW_PATH).parent.mkdir(parents=True)
    (redump.path / WORKFLOW_PATH).write_text('old\n')
    git, gh = FakeGit({redump.path: FakeRepo(fail_on={'commit_paths'})}), FakeGh()
    ci_sync(redump, git, gh)
    assert (redump.path / WORKFLOW_PATH).read_text() == 'old\n'


def test_lint_rules_are_explicit(sample_workspace: Path):
    """Lint must not depend on ruff's defaults (they grow between releases) nor on the workspace's ALL config.

    Only pyflakes + syntax errors: real bugs, and no RUF100 clash with the workspace's ``# noqa`` comments.
    """
    text = render_ci(discover(sample_workspace).packages['datoso_seed_redump'])
    assert 'run: ruff check --isolated --select E4,E7,E9,F .' in text
