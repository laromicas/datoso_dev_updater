"""Release tasks up to "PR opened": branch, bump (version + dependency floors), commit, push, PR.

Every task checks whether it is already done, so re-running resumes instead of duplicating work:
an open PR for the release branch means done; a release branch on origin that already carries the target version
skips straight to opening the PR. A failed edit/commit restores the files, so the default branch is never left dirty.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum

from packaging.version import InvalidVersion, Version

from datoso_dev.planner import PackagePlan, Plan
from datoso_dev.ports import GhError, GhPort, GitError, GitPort
from datoso_dev.pyproject import set_floor, set_init_version, set_project_version
from datoso_dev.state import DEFAULT_BRANCH, PackageState
from datoso_dev.workspace import VERSION_LINE, Package, Workspace

PYPROJECT = 'pyproject.toml'

Progress = Callable[[str], None]


class Outcome(StrEnum):
    """Result of trying to open one release PR."""

    OPENED = 'PR opened'
    ALREADY_OPEN = 'PR already open'
    NOTHING_TO_COMMIT = 'nothing to commit'
    ERROR = 'error'


@dataclass(frozen=True)
class ReleaseResult:
    """What happened to one package."""

    name: str
    outcome: Outcome
    detail: str = ''
    pr_url: str | None = None


def _version_file(pkg: Package) -> str:
    return str(pkg.version_file.relative_to(pkg.path)) if pkg.version_file else PYPROJECT


def _version_in(text: str | None, pkg: Package) -> Version | None:
    if text is None:
        return None
    try:
        if pkg.version_file:
            match = VERSION_LINE.search(text)
            return Version(match[1]) if match else None
        return Version(tomllib.loads(text)['project']['version'])
    except (InvalidVersion, KeyError, tomllib.TOMLDecodeError):
        return None


def _edits(item: PackagePlan, pkg: Package) -> dict[str, tuple[str, str]]:
    """Relative path → (original, edited) for every file the release changes."""
    texts: dict[str, str] = {}

    def read(rel: str) -> str:
        if rel not in texts:
            texts[rel] = (pkg.path / rel).read_text(encoding='utf-8')
        return texts[rel]

    edited: dict[str, str] = {}
    version_rel = _version_file(pkg)
    if item.target != item.current:
        setter = set_init_version if pkg.version_file else set_project_version
        edited[version_rel] = setter(read(version_rel), item.target)
    for change in item.floor_changes:
        current = edited.get(PYPROJECT, read(PYPROJECT))
        edited[PYPROJECT], _ = set_floor(current, change.dist, change.target)
    return {rel: (texts[rel], new) for rel, new in edited.items() if new != texts[rel]}


def pr_body(item: PackagePlan, subjects: list[str]) -> str:
    """Release PR description: version change, dependency floors, commits being released."""
    since = f'v{item.released}' if item.released else 'the first commit'
    lines = [f'Release of `{item.name}` **{item.tag}** ({item.released or "unreleased"} → {item.target}).', '']
    if item.floor_changes:
        lines += ['Dependency floors (policy: follow the latest core and IA plugin):']
        lines += [f'- `{c.dist}>={c.current}` → `{c.dist}>={c.target}`' for c in item.floor_changes]
        lines.append('')
    lines.append(f'Changes since {since}:')
    lines += [f'- {subject}' for subject in subjects] or ['- (no commits)']
    lines += ['', 'Opened by `datoso-dev release`. GitHub Release notes are generated when it is published.']
    return '\n'.join(lines)


def open_release_pr(  # noqa: PLR0913
    item: PackagePlan,
    pkg: Package,
    git: GitPort,
    gh: GhPort,
    *,
    since: str | None,
    default_branch: str = DEFAULT_BRANCH,
    progress: Progress | None = None,
) -> ReleaseResult:
    """Branch, bump, commit, push and open the release PR for one package (resumable)."""
    report = progress or (lambda _msg: None)
    repo = pkg.path
    try:
        report('fetching')
        git.fetch(repo)
        existing = gh.pr_for_branch(repo, item.branch)
        if existing:
            return ReleaseResult(item.name, Outcome.ALREADY_OPEN, item.branch, existing)
        subjects = git.log_subjects(repo, since, f'origin/{default_branch}')
        remote = git.show_file(repo, f'origin/{item.branch}', _version_file(pkg))
        if _version_in(remote, pkg) != item.target and not _commit_bump(item, pkg, git, default_branch, report):
            return ReleaseResult(
                item.name, Outcome.NOTHING_TO_COMMIT, f'{item.target} is already on {default_branch}; tag it'
            )
        report('opening PR')
        url = gh.pr_create(repo, base=default_branch, head=item.branch, title=item.title, body=pr_body(item, subjects))
    except (GitError, GhError, OSError) as exc:
        return ReleaseResult(item.name, Outcome.ERROR, str(exc))
    return ReleaseResult(item.name, Outcome.OPENED, item.branch, url)


def _commit_bump(item: PackagePlan, pkg: Package, git: GitPort, default_branch: str, report: Progress) -> bool:
    """Create the release branch and commit the edits on it. Return False when there is nothing to change."""
    repo = pkg.path
    report('creating branch')
    git.create_branch(repo, item.branch, start=f'origin/{default_branch}')
    edits: dict[str, tuple[str, str]] = {}
    committed = False
    try:
        edits = _edits(item, pkg)
        if not edits:
            return False
        for rel, (_, new) in edits.items():
            (repo / rel).write_text(new, encoding='utf-8')
        report('committing')
        git.commit_paths(repo, list(edits), item.title)
        committed = True
        report('pushing')
        git.push(repo, item.branch)
    finally:
        if not committed:
            for rel, (original, _) in edits.items():
                (repo / rel).write_text(original, encoding='utf-8')
        git.switch(repo, default_branch)
    return True


def open_release_prs(  # noqa: PLR0913
    plan: Plan,
    ws: Workspace,
    states: Mapping[str, PackageState],
    git: GitPort,
    gh: GhPort,
    *,
    on_progress: Callable[[str, str], None] | None = None,
    default_branch: str = DEFAULT_BRANCH,
    workers: int = 8,
) -> list[ReleaseResult]:
    """Open release PRs for the runnable packages of ``plan`` (step 1, unblocked), in parallel."""
    notify = on_progress or (lambda _name, _msg: None)

    def run(item: PackagePlan) -> ReleaseResult:
        result = open_release_pr(
            item,
            ws.packages[item.name],
            git,
            gh,
            since=states[item.name].last_tag,
            default_branch=default_branch,
            progress=lambda msg: notify(item.name, msg),
        )
        notify(item.name, str(result.outcome))
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run, plan.runnable_now))
