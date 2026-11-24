"""`datoso-dev pr`: push committed feature branches and open (or update) one linked PR per repository.

It never commits: a repo with uncommitted changes is skipped, so nothing unreviewed (cookies, local configs) is sent.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from enum import StrEnum

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from datoso_dev.policy import current_floor
from datoso_dev.ports import GhError, GhPort, GitError, GitPort, PyPIPort
from datoso_dev.state import DEFAULT_BRANCH
from datoso_dev.workspace import Package, Workspace

RELATED_START = '<!-- datoso-dev:related -->'
RELATED_END = '<!-- /datoso-dev:related -->'
SECTION = re.compile(r'^=== (\S+)', re.MULTILINE)
COMMENT = re.compile(r'<!--.*?-->', re.DOTALL)
PR_URL = re.compile(r'github\.com/([^/]+/[^/]+)/pull/(\d+)')


class PrAction(StrEnum):
    """What ``pr`` does (or did) for one repository."""

    OPEN = 'open PR'
    UPDATE = 'update PR'
    UP_TO_DATE = 'up to date'
    SKIPPED = 'skipped'
    ERROR = 'error'


@dataclass(frozen=True)
class PrItem:
    """One repository's feature branch: the plan, then the outcome."""

    package: Package
    branch: str
    action: PrAction
    detail: str = ''
    commits: tuple[str, ...] = ()  # subjects ahead of the base branch, newest first
    unpushed: bool = False
    draft_reason: str = ''  # set when a workspace floor is not on PyPI yet
    pr_url: str | None = None
    mark_ready: bool = False  # an open draft whose floors are now released
    done: bool = False

    @property
    def actionable(self) -> bool:
        """Whether running the plan changes something."""
        return self.action in {PrAction.OPEN, PrAction.UPDATE}


@dataclass
class PrText:
    """Title and description for new PRs, plus a note per repository."""

    title: str
    body: str = ''
    notes: dict[str, str] = field(default_factory=dict)


def unreleased_floors(ws: Workspace, pkg: Package, pypi: PyPIPort) -> str:
    """Workspace dependency floors of ``pkg`` that PyPI doesn't have yet, e.g. ``datoso>=1.3.0``."""
    index = ws.by_dist_name()
    missing = []
    for req in pkg.requirements:
        floor = current_floor(req)
        if floor is None or canonicalize_name(req.name) not in index:
            continue
        latest = pypi.latest_version(canonicalize_name(req.name))
        try:
            released = latest is not None and Version(latest) >= floor
        except InvalidVersion:
            released = False
        if not released:
            missing.append(f'{req.name}>={floor}')
    return f'needs {", ".join(missing)} (not on PyPI yet)' if missing else ''


def plan_pr(ws: Workspace, pkg: Package, git: GitPort, gh: GhPort, pypi: PyPIPort) -> PrItem | None:  # noqa: PLR0911
    """Plan one repository; ``None`` when it has no feature work (clean, on the base branch)."""
    repo, base = pkg.path, DEFAULT_BRANCH
    try:
        git.fetch(repo)
        branch = git.current_branch(repo)
        dirty = git.changed_files(repo)
        commits = tuple(git.log_subjects(repo, f'origin/{base}', 'HEAD'))
        if branch == base:
            if commits:
                return PrItem(pkg, branch, PrAction.SKIPPED, f'commits on {base}: move them to a branch', commits)
            return None
        if branch == 'HEAD':
            return PrItem(pkg, branch, PrAction.SKIPPED, 'detached HEAD')
        if dirty:
            return PrItem(pkg, branch, PrAction.SKIPPED, 'uncommitted changes (commit them first)', commits)
        if not commits:
            return PrItem(pkg, branch, PrAction.SKIPPED, f'nothing ahead of {base}')
        existing = gh.pr_for_branch(repo, branch)
        if not existing and gh.pr_for_branch(repo, branch, state='merged'):
            return PrItem(pkg, branch, PrAction.SKIPPED, f'already merged (switch to {base})', commits)
        counts = git.ahead_behind(repo)
        if counts and counts[1]:
            return PrItem(pkg, branch, PrAction.SKIPPED, f'behind origin/{branch} by {counts[1]} (pull first)')
        unpushed = counts is None or counts[0] > 0
        draft_reason = unreleased_floors(ws, pkg, pypi)
        if not existing:
            return PrItem(pkg, branch, PrAction.OPEN, draft_reason, commits, unpushed, draft_reason)
        mark_ready = not draft_reason and gh.pr_is_draft(repo, existing)
        action = PrAction.UPDATE if unpushed or mark_ready else PrAction.UP_TO_DATE
        detail = ', '.join(filter(None, ['push' if unpushed else '', 'mark ready' if mark_ready else '', draft_reason]))
        return PrItem(pkg, branch, action, detail, commits, unpushed, draft_reason, existing, mark_ready)
    except (GitError, GhError) as exc:
        return PrItem(pkg, '?', PrAction.ERROR, str(exc))


def plan_prs(  # noqa: PLR0913
    ws: Workspace, packages: Iterable[Package], git: GitPort, gh: GhPort, pypi: PyPIPort, *, workers: int = 8
) -> list[PrItem]:
    """Plan every package (one repo per thread); repos without feature work are left out."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        items = pool.map(lambda pkg: plan_pr(ws, pkg, git, gh, pypi), packages)
        return [item for item in items if item is not None]


def pr_body(text: PrText, item: PrItem) -> str:
    """Return the description of a new PR: the shared text, the repo's note, its commits and why it is a draft."""
    parts = [text.body.strip(), text.notes.get(item.package.name, '').strip()]
    parts.append('Commits:\n' + '\n'.join(f'- {subject}' for subject in item.commits))
    if item.draft_reason:
        parts.append(f'Draft: {item.draft_reason}.')
    return '\n\n'.join(part for part in parts if part) + '\n'


def run_pr(item: PrItem, git: GitPort, gh: GhPort, text: PrText | None, base: str = DEFAULT_BRANCH) -> PrItem:
    """Push the branch and open the PR (draft when floors are unreleased), or update the open one."""
    if not item.actionable:
        return item
    repo = item.package.path
    try:
        if item.unpushed:
            git.push(repo, item.branch)
        url = item.pr_url
        if item.action is PrAction.OPEN:
            if text is None:
                return replace(item, action=PrAction.ERROR, detail='no title given for the new PR')
            url = gh.pr_create(
                repo, base=base, head=item.branch, title=text.title, body=pr_body(text, item),
                draft=bool(item.draft_reason),
            )  # fmt: skip
        elif item.mark_ready:
            gh.pr_ready(repo, url)
    except (GitError, GhError) as exc:
        return replace(item, action=PrAction.ERROR, detail=str(exc))
    return replace(item, pr_url=url, done=True)


def pr_ref(url: str) -> str:
    """``owner/repo#N`` for a PR URL (GitHub links it across repositories)."""
    match = PR_URL.search(url)
    return f'{match.group(1)}#{match.group(2)}' if match else url


def with_related(body: str, refs: Iterable[str]) -> str:
    """``body`` with its datoso-dev "Related" section replaced (or appended)."""
    section = f'{RELATED_START}\nRelated: {", ".join(refs)}\n{RELATED_END}'
    pattern = re.compile(re.escape(RELATED_START) + '.*?' + re.escape(RELATED_END), re.DOTALL)
    if pattern.search(body):
        return pattern.sub(lambda _: section, body)
    return f'{body.rstrip()}\n\n{section}\n'


def link_prs(items: Iterable[PrItem], gh: GhPort) -> list[str]:
    """Cross-link every PR of the run; return the errors (linking is best effort)."""
    linked = [item for item in items if item.pr_url and item.action is not PrAction.ERROR]
    errors = []
    if len(linked) < 2:  # noqa: PLR2004
        return errors
    for item in linked:
        refs = [pr_ref(other.pr_url) for other in linked if other is not item]
        try:
            body = gh.pr_body(item.package.path, item.pr_url)
            updated = with_related(body, refs)
            if updated != body:
                gh.pr_edit_body(item.package.path, item.pr_url, updated)
        except GhError as exc:
            errors.append(f'{item.package.name}: {exc}')
    return errors


def editor_template(items: Iterable[PrItem], title: str = '') -> str:
    """Text the user edits: title, shared description, then a note section per new PR."""
    lines = [
        title,
        '',
        '<!-- Line 1: title of the new PRs. Then the description they share. -->',
        '<!-- Under each "=== <package>" line: a note for that repo only (its commits are added for you). -->',
        '<!-- Comments are removed; an empty title cancels. -->',
        '',
    ]
    for item in items:
        if item.action is not PrAction.OPEN:
            continue
        commits = ''.join(f'\n     {subject}' for subject in item.commits)
        lines += ['', f'=== {item.package.name}', f'<!-- {item.branch}:{commits}\n-->', '']
    return '\n'.join(lines) + '\n'


def parse_template(text: str) -> PrText | None:
    """Read the edited template; ``None`` when the title is empty."""
    text = COMMENT.sub('', text)
    chunks = SECTION.split(text)
    head = chunks[0].strip().splitlines()
    if not head or not head[0].strip():
        return None
    notes = {name: note.strip() for name, note in zip(chunks[1::2], chunks[2::2], strict=True) if note.strip()}
    return PrText(head[0].strip(), '\n'.join(head[1:]).strip(), notes)


def default_title(items: Iterable[PrItem]) -> str:
    """Newest commit subject of the first new PR."""
    return next((item.commits[0] for item in items if item.action is PrAction.OPEN and item.commits), '')


def run_prs(items: Iterable[PrItem], git: GitPort, gh: GhPort, text: PrText | None) -> tuple[list[PrItem], list[str]]:
    """Run every plan in order, then cross-link the PRs; return the outcomes and linking errors."""
    results = [run_pr(item, git, gh, text) for item in items]
    return results, link_prs(results, gh)
