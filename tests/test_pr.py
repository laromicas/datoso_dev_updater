"""Tests for `datoso-dev pr`: planning, pushing, opening drafts and cross-linking PRs."""

from __future__ import annotations

from pathlib import Path

import pytest

from datoso_dev.pr import (
    RELATED_START,
    PrAction,
    PrItem,
    PrText,
    default_title,
    editor_template,
    link_prs,
    parse_template,
    plan_pr,
    plan_prs,
    pr_body,
    pr_ref,
    run_pr,
    run_prs,
    unreleased_floors,
    with_related,
)
from datoso_dev.workspace import Workspace, discover

from .fakes import FakeGh, FakeGit, FakePyPI, FakeRepo

BRANCH = 'feat/download-progress'


@pytest.fixture
def ws(sample_workspace: Path) -> Workspace:
    return discover(sample_workspace)


@pytest.fixture
def released(ws: Workspace) -> FakePyPI:
    return FakePyPI({pkg.dist_name: str(pkg.version) for pkg in ws.packages.values()})


def feature(**kwargs: object) -> FakeRepo:
    state = {'branch': BRANCH, 'subjects': ['Show progress', 'Add bar'], 'has_upstream': False, **kwargs}
    return FakeRepo(**state)


def setup(ws: Workspace, **repos: FakeRepo) -> FakeGit:
    states = {pkg.path: FakeRepo() for pkg in ws.packages.values()}
    states.update({ws.packages[name].path: repo for name, repo in repos.items()})
    return FakeGit(states)


# --------------------------------------------------------------------------- planning


def test_clean_master_has_no_feature_work(ws, released):
    git = setup(ws)
    assert plan_pr(ws, ws.packages['datoso_seed_redump'], git, FakeGh(), released) is None


def test_commits_on_master_are_skipped(ws, released):
    git = setup(ws, datoso_seed_redump=FakeRepo(subjects=['oops']))
    item = plan_pr(ws, ws.packages['datoso_seed_redump'], git, FakeGh(), released)
    assert (item.action, item.detail) == (PrAction.SKIPPED, 'commits on master: move them to a branch')


@pytest.mark.parametrize(
    ('repo', 'detail'),
    [
        (feature(changed=['config.ini']), 'uncommitted changes (commit them first)'),
        (feature(subjects=[]), 'nothing ahead of master'),
        (feature(branch='HEAD'), 'detached HEAD'),
        (feature(has_upstream=True, behind=2), 'behind origin/feat/download-progress by 2 (pull first)'),
    ],
)
def test_unsafe_branches_are_skipped(ws, released, repo, detail):
    item = plan_pr(ws, ws.packages['datoso_seed_redump'], setup(ws, datoso_seed_redump=repo), FakeGh(), released)
    assert (item.action, item.detail) == (PrAction.SKIPPED, detail)


def test_merged_branch_is_skipped(ws, released):
    git = setup(ws, datoso_seed_redump=feature())
    gh = FakeGh()
    gh.merged_prs[(ws.packages['datoso_seed_redump'].path, BRANCH)] = 'https://github.com/o/r/pull/1'
    item = plan_pr(ws, ws.packages['datoso_seed_redump'], git, gh, released)
    assert (item.action, item.detail) == (PrAction.SKIPPED, 'already merged (switch to master)')


def test_git_failure_is_an_error(ws, released):
    git = setup(ws, datoso_seed_redump=feature(fail_on={'fetch'}))
    item = plan_pr(ws, ws.packages['datoso_seed_redump'], git, FakeGh(), released)
    assert item.action is PrAction.ERROR
    assert 'fetch failed' in item.detail


def test_new_branch_opens_a_pr(ws, released):
    git = setup(ws, datoso_seed_redump=feature())
    item = plan_pr(ws, ws.packages['datoso_seed_redump'], git, FakeGh(), released)
    assert (item.action, item.unpushed, item.draft_reason) == (PrAction.OPEN, True, '')
    assert item.commits == ('Show progress', 'Add bar')


def test_unreleased_core_floor_makes_a_draft(ws):
    pypi = FakePyPI({'datoso': '1.0.0'})
    git = setup(ws, datoso_seed_redump=feature())
    item = plan_pr(ws, ws.packages['datoso_seed_redump'], git, FakeGh(), pypi)
    assert item.draft_reason == 'needs datoso>=1.1.0 (not on PyPI yet)'
    assert item.detail == item.draft_reason


def test_unreleased_floors_ignores_outside_and_unparsable(ws):
    pkg = ws.packages['datoso_plugin_internetarchive']  # internetarchive is not a workspace package
    assert unreleased_floors(ws, pkg, FakePyPI({'datoso': 'not-a-version'})) == 'needs datoso>=1.1.0 (not on PyPI yet)'
    assert unreleased_floors(ws, pkg, FakePyPI({'datoso': '1.1.0'})) == ''


def test_open_pr_with_unpushed_commits_is_updated(ws, released):
    path = ws.packages['datoso_seed_redump'].path
    git = setup(ws, datoso_seed_redump=feature(has_upstream=True, ahead=1))
    gh = FakeGh({(path, BRANCH): 'https://github.com/o/redump/pull/3'})
    item = plan_pr(ws, ws.packages['datoso_seed_redump'], git, gh, released)
    assert (item.action, item.detail, item.pr_url) == (PrAction.UPDATE, 'push', 'https://github.com/o/redump/pull/3')


def test_pushed_open_pr_is_up_to_date(ws, released):
    path = ws.packages['datoso_seed_redump'].path
    git = setup(ws, datoso_seed_redump=feature(has_upstream=True))
    gh = FakeGh({(path, BRANCH): 'https://github.com/o/redump/pull/3'})
    assert plan_pr(ws, ws.packages['datoso_seed_redump'], git, gh, released).action is PrAction.UP_TO_DATE


def test_draft_is_marked_ready_once_floors_are_released(ws, released):
    path = ws.packages['datoso_seed_redump'].path
    url = 'https://github.com/o/redump/pull/3'
    gh = FakeGh({(path, BRANCH): url})
    gh.drafts.add(url)
    git = setup(ws, datoso_seed_redump=feature(has_upstream=True))
    item = plan_pr(ws, ws.packages['datoso_seed_redump'], git, gh, released)
    assert (item.action, item.detail, item.mark_ready) == (PrAction.UPDATE, 'mark ready', True)


def test_plan_prs_leaves_out_repos_without_feature_work(ws, released):
    git = setup(ws, datoso_seed_redump=feature(), datoso=feature())
    items = plan_prs(ws, ws.packages.values(), git, FakeGh(), released)
    assert [item.package.name for item in items] == ['datoso', 'datoso_seed_redump']


# --------------------------------------------------------------------------- running


def test_run_prs_pushes_opens_drafts_and_links(ws):
    pypi = FakePyPI({'datoso': '1.1.1'})  # the plugin is unreleased, so enhanced is a draft
    git = setup(ws, datoso=feature(), datoso_seed_enhanced=feature(subjects=['Use plugin'], has_upstream=True))
    gh = FakeGh()
    core = ws.packages['datoso'].path
    existing = 'https://github.com/laromicas/datoso_seed_enhanced/pull/9'
    gh.open_prs[(ws.packages['datoso_seed_enhanced'].path, BRANCH)] = existing
    gh.bodies[existing] = 'Earlier description'
    items = plan_prs(ws, ws.packages.values(), git, gh, pypi)
    text = PrText('Download progress', 'Shared text', {'datoso': 'Core note'})
    results, errors = run_prs(items, git, gh, text)
    assert errors == []
    assert git.pushes == [(core, BRANCH, False)]
    created = gh.created[0]
    assert (created['title'], created['draft'], created['base']) == ('Download progress', False, 'master')
    assert created['body'].startswith('Shared text\n\nCore note\n\nCommits:\n- Show progress\n- Add bar')
    assert results[0].done
    assert results[0].pr_url == created['url']
    assert results[1].action is PrAction.UP_TO_DATE  # floors unreleased, but an open PR is never turned into a draft
    assert gh.bodies[existing].startswith('Earlier description\n\n' + RELATED_START)
    assert 'owner/datoso#1' in gh.bodies[existing]
    assert 'laromicas/datoso_seed_enhanced#9' in gh.bodies[created['url']]


def test_draft_body_says_why(ws):
    item = PrItem(ws.packages['datoso_seed_redump'], BRANCH, PrAction.OPEN, commits=('A',), draft_reason='needs x')
    assert pr_body(PrText('T'), item) == 'Commits:\n- A\n\nDraft: needs x.\n'


def test_run_pr_opens_draft_and_marks_ready(ws):
    gh = FakeGh()
    pkg = ws.packages['datoso_seed_redump']
    git = setup(ws)
    opened = run_pr(PrItem(pkg, BRANCH, PrAction.OPEN, draft_reason='needs x'), git, gh, PrText('T'))
    assert gh.created[0]['draft'] is True
    assert git.pushes == []  # already pushed
    ready = run_pr(replace_action(opened, PrAction.UPDATE, mark_ready=True), git, gh, None)
    assert gh.readied == [opened.pr_url]
    assert ready.done


def replace_action(item: PrItem, action: PrAction, **kwargs: object) -> PrItem:
    from dataclasses import replace  # noqa: PLC0415

    return replace(item, action=action, done=False, **kwargs)


def test_run_pr_needs_a_title_to_open(ws):
    item = PrItem(ws.packages['datoso_seed_redump'], BRANCH, PrAction.OPEN)
    result = run_pr(item, setup(ws), FakeGh(), None)
    assert (result.action, result.detail) == (PrAction.ERROR, 'no title given for the new PR')


def test_run_pr_maps_failures(ws):
    item = PrItem(ws.packages['datoso_seed_redump'], BRANCH, PrAction.OPEN, unpushed=True)
    result = run_pr(item, setup(ws, datoso_seed_redump=FakeRepo(fail_on={'push'})), FakeGh(), PrText('T'))
    assert result.action is PrAction.ERROR
    result = run_pr(replace_action(item, PrAction.OPEN, unpushed=False), setup(ws), FakeGh(fail=True), PrText('T'))
    assert 'gh pr create failed' in result.detail


def test_skipped_items_are_left_alone(ws):
    item = PrItem(ws.packages['datoso_seed_redump'], BRANCH, PrAction.SKIPPED)
    assert run_pr(item, setup(ws), FakeGh(), None) is item


# --------------------------------------------------------------------------- linking


def test_pr_ref():
    assert pr_ref('https://github.com/laromicas/datoso/pull/16') == 'laromicas/datoso#16'
    assert pr_ref('not a url') == 'not a url'


def test_with_related_replaces_its_own_section():
    once = with_related('Body', ['o/a#1'])
    twice = with_related(once, ['o/a#1', 'o/b#2'])
    assert twice.count(RELATED_START) == 1
    assert twice.startswith('Body\n\n')
    assert 'Related: o/a#1, o/b#2' in twice


def test_link_prs_needs_two_and_reports_errors(ws):
    one = PrItem(ws.packages['datoso'], BRANCH, PrAction.UP_TO_DATE, pr_url='https://github.com/o/datoso/pull/1')
    gh = FakeGh()
    assert link_prs([one], gh) == []
    two = PrItem(ws.packages['datoso_seed_redump'], BRANCH, PrAction.UP_TO_DATE, pr_url='https://github.com/o/r/pull/2')
    gh.fail_body = True
    errors = link_prs([one, two], gh)
    assert len(errors) == 2
    assert errors[0].startswith('datoso: gh pr view failed')


def test_link_prs_does_not_rewrite_linked_bodies(ws):
    one = PrItem(ws.packages['datoso'], BRANCH, PrAction.UP_TO_DATE, pr_url='https://github.com/o/datoso/pull/1')
    two = PrItem(ws.packages['datoso_seed_redump'], BRANCH, PrAction.UP_TO_DATE, pr_url='https://github.com/o/r/pull/2')
    gh = FakeGh()
    link_prs([one, two], gh)
    gh.edited.clear()
    link_prs([one, two], gh)
    assert gh.edited == []


# --------------------------------------------------------------------------- editor text


def test_editor_template_round_trip(ws):
    items = [
        PrItem(ws.packages['datoso'], BRANCH, PrAction.OPEN, commits=('Core change',)),
        PrItem(ws.packages['datoso_seed_redump'], BRANCH, PrAction.OPEN, commits=('Seed change',)),
        PrItem(ws.packages['datoso_seed_enhanced'], BRANCH, PrAction.UPDATE),
    ]
    template = editor_template(items, default_title(items))
    assert template.startswith('Core change\n')
    assert '=== datoso_seed_enhanced' not in template  # only new PRs get a note
    edited = template.replace('=== datoso_seed_redump\n', '=== datoso_seed_redump\nSeed note\n')
    edited = edited.replace('Core change\n', 'Download progress\nShared\nlines\n', 1)
    text = parse_template(edited)
    assert (text.title, text.body) == ('Download progress', 'Shared\nlines')
    assert text.notes == {'datoso_seed_redump': 'Seed note'}


def test_empty_title_cancels():
    assert parse_template('<!-- only a comment -->\n\n') is None
    assert parse_template('') is None


def test_default_title_without_new_prs(ws):
    assert default_title([PrItem(ws.packages['datoso'], BRANCH, PrAction.UPDATE, commits=('x',))]) == ''
