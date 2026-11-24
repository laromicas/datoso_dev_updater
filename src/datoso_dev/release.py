"""Release tasks: open the release PR, then (once it is merged) tag, publish the GitHub Release and wait for PyPI.

A release takes two runs of ``datoso-dev release`` with the merge in between. Each run first *probes* every package
(:func:`probe_release`) to find what is left: no PR → open one; an open PR → merge it first; a merged PR → tag and
publish; a GitHub Release for the tag → done. So re-running always resumes instead of duplicating work. Opening a PR
is itself resumable (a release branch on origin that already carries the target version skips straight to the PR),
and a failed edit/commit restores the files, so the default branch is never left dirty.
"""

from __future__ import annotations

import time
import tomllib
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from datoso_dev.planner import PackagePlan, Plan
from datoso_dev.ports import Checks, GhError, GhPort, GitError, GitPort, PyPIPort
from datoso_dev.pyproject import set_floor, set_init_version, set_project_version
from datoso_dev.state import DEFAULT_BRANCH, PackageState
from datoso_dev.workspace import VERSION_LINE, Package, Workspace

PYPROJECT = 'pyproject.toml'
PYPI_TIMEOUT = 600.0  # seconds to wait for the publish workflow to upload
PYPI_INTERVAL = 15.0
CHECKS_TIMEOUT = 1800.0  # seconds for CI to finish
CHECKS_GRACE = 120.0  # seconds for the first checks to be reported on a new PR

Progress = Callable[[str], None]


class Outcome(StrEnum):
    """What happened to one package."""

    OPENED = 'PR opened'
    ALREADY_OPEN = 'PR open'  # merge it, then run `release` again
    MERGED = 'PR merged'  # only the tag + GitHub Release are left
    CHECKS_FAILED = 'CI failed'  # PR left open
    PUBLISHED = 'released'
    PYPI_PENDING = 'released, PyPI pending'
    ALREADY_RELEASED = 'already released'
    NOTHING_TO_COMMIT = 'nothing to commit'
    BLOCKED = 'not started'  # a dependency in the batch didn't finish
    UPDATED = 'updated'  # core extras floors / workspace pointers
    SKIPPED = 'skipped'
    ERROR = 'error'


class Action(StrEnum):
    """What a run will do for one package, found by :func:`probe_release`."""

    OPEN_PR = 'open release PR'
    WAIT_MERGE = 'PR open: merge it first'
    PUBLISH = 'tag + publish release'
    RELEASED = 'already released'
    ERROR = 'error'


@dataclass(frozen=True)
class Probe:
    """The next action for one package, and the PR it concerns."""

    action: Action
    pr_url: str | None = None
    detail: str = ''


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
        merged = gh.pr_for_branch(repo, item.branch, state='merged')  # done, even if a duplicate is open
        if merged:
            detail = f'next: tag {item.tag} and publish its GitHub Release'
            return ReleaseResult(item.name, Outcome.MERGED, detail, merged)
        existing = gh.pr_for_branch(repo, item.branch)
        if existing:
            return ReleaseResult(item.name, Outcome.ALREADY_OPEN, item.branch, existing)
        subjects = git.log_subjects(repo, since, f'origin/{default_branch}')
        remote = git.show_file(repo, f'origin/{item.branch}', _version_file(pkg))
        if _version_in(remote, pkg) != item.target and not _commit_bump(item, pkg, git, default_branch, report):
            return ReleaseResult(item.name, Outcome.NOTHING_TO_COMMIT, f'{item.target} is already on {default_branch}')
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


def probe_release(item: PackagePlan, pkg: Package, git: GitPort, gh: GhPort) -> Probe:
    """Find what is left to do for ``item`` (read-only: fetch + GitHub queries)."""
    repo = pkg.path
    try:
        git.fetch(repo)
        release = gh.release_url(repo, item.tag)
        if release:
            return Probe(Action.RELEASED, release)
        merged = gh.pr_for_branch(repo, item.branch, state='merged')  # wins over a stale duplicate that is open
        if merged:
            return Probe(Action.PUBLISH, merged)
        existing = gh.pr_for_branch(repo, item.branch)
        if existing:
            return Probe(Action.WAIT_MERGE, existing)
    except (GitError, GhError, OSError) as exc:
        return Probe(Action.ERROR, detail=str(exc))
    return Probe(Action.OPEN_PR)


def probe_releases(plan: Plan, ws: Workspace, git: GitPort, gh: GhPort, *, workers: int = 8) -> dict[str, Probe]:
    """Probe the runnable packages of ``plan`` in parallel."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        probes = pool.map(lambda item: probe_release(item, ws.packages[item.name], git, gh), plan.runnable_now)
        return {item.name: probe for item, probe in zip(plan.runnable_now, probes, strict=True)}


def wait_for_pypi(  # noqa: PLR0913
    pypi: PyPIPort,
    dist_name: str,
    version: str,
    *,
    timeout: float = PYPI_TIMEOUT,
    interval: float = PYPI_INTERVAL,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Poll PyPI until ``version`` is listed; ``False`` after ``timeout`` seconds. Network errors just retry."""
    deadline = clock() + timeout
    while True:
        try:
            if pypi.has_version(dist_name, version):
                return True
        except (OSError, ValueError):
            pass
        if clock() >= deadline:
            return False
        sleep(interval)


def publish_release(  # noqa: PLR0913
    item: PackagePlan,
    pkg: Package,
    gh: GhPort,
    pypi: PyPIPort,
    *,
    pr_url: str,
    since: str | None,
    target: str | None = None,
    wait: bool = True,
    progress: Progress | None = None,
    timeout: float = PYPI_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> ReleaseResult:
    """Tag the release PR's merge commit (or ``target``), publish its GitHub Release and wait for PyPI."""
    report = progress or (lambda _msg: None)
    repo = pkg.path
    try:
        target = target or gh.pr_merge_commit(repo, pr_url)
        report(f'publishing GitHub Release {item.tag}')
        url = gh.release_create(
            repo,
            tag=item.tag,
            target=target,
            title=item.tag,
            notes_start_tag=since,
            prerelease=item.target.is_prerelease,
        )
    except (GhError, OSError) as exc:
        return ReleaseResult(item.name, Outcome.ERROR, str(exc))
    if not pkg.pypi:
        return ReleaseResult(item.name, Outcome.PUBLISHED, 'GitHub Release (not published to PyPI)', url)
    if not wait:
        return ReleaseResult(item.name, Outcome.PUBLISHED, 'GitHub Release; PyPI upload not awaited', url)
    report('waiting for PyPI')
    if wait_for_pypi(pypi, pkg.dist_name, str(item.target), timeout=timeout, sleep=sleep, clock=clock):
        return ReleaseResult(item.name, Outcome.PUBLISHED, f'GitHub Release; {item.target} on PyPI', url)
    minutes = round(timeout / 60)
    detail = f'{item.target} not on PyPI after {minutes} min: check the publish workflow (Actions tab)'
    return ReleaseResult(item.name, Outcome.PYPI_PENDING, detail, url)


def publish_from_branch(  # noqa: PLR0913
    item: PackagePlan,
    pkg: Package,
    git: GitPort,
    gh: GhPort,
    pypi: PyPIPort,
    *,
    since: str | None,
    default_branch: str = DEFAULT_BRANCH,
    **publish: Any,  # noqa: ANN401
) -> ReleaseResult:
    """Publish when the version is already on the default branch (no release PR needed): tag its current commit."""
    try:
        target = git.rev_parse(pkg.path, f'origin/{default_branch}')
    except GitError as exc:
        return ReleaseResult(item.name, Outcome.ERROR, str(exc))
    return publish_release(item, pkg, gh, pypi, pr_url='', since=since, target=target, **publish)


def wait_for_checks(  # noqa: PLR0913
    gh: GhPort,
    repo: Path,
    pr_url: str,
    *,
    timeout: float = CHECKS_TIMEOUT,
    grace: float = CHECKS_GRACE,
    interval: float = PYPI_INTERVAL,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[bool, str]:
    """Poll the PR's CI until it passes (``True``) or fails, reports nothing, or times out (``False``, why)."""
    start = clock()
    while True:
        state, detail = gh.pr_checks(repo, pr_url)
        elapsed = clock() - start
        if state is Checks.PASSED:
            return True, ''
        if state is Checks.FAILED:
            return False, detail or 'a check failed'
        if state is Checks.NONE and elapsed >= grace:
            return False, f'no CI checks reported after {round(grace)} s'
        if elapsed >= timeout:
            return False, f'still running after {round(timeout / 60)} min'
        sleep(interval)


def update_master(git: GitPort, repo: Path, default_branch: str = DEFAULT_BRANCH) -> None:
    """Fast-forward the local default branch after a merge, when the checkout is clean and on it (best effort)."""
    try:
        git.fetch(repo)
        if git.current_branch(repo) == default_branch and not git.changed_files(repo):
            git.pull_rebase(repo)
    except GitError:
        pass


def merge_release_pr(  # noqa: PLR0913
    item: PackagePlan,
    pkg: Package,
    git: GitPort,
    gh: GhPort,
    *,
    pr_url: str,
    progress: Progress | None = None,
    default_branch: str = DEFAULT_BRANCH,
    timeout: float = CHECKS_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> ReleaseResult:
    """Wait for the release PR's CI, squash-merge it and fast-forward the local default branch."""
    report = progress or (lambda _msg: None)
    try:
        report('waiting for CI')
        passed, why = wait_for_checks(gh, pkg.path, pr_url, timeout=timeout, sleep=sleep, clock=clock)
        if not passed:
            return ReleaseResult(item.name, Outcome.CHECKS_FAILED, f'{why}; the PR stays open', pr_url)
        report('merging')
        gh.pr_merge(pkg.path, pr_url)
    except (GhError, OSError) as exc:
        return ReleaseResult(item.name, Outcome.ERROR, str(exc), pr_url)
    update_master(git, pkg.path, default_branch)
    return ReleaseResult(item.name, Outcome.MERGED, 'merged', pr_url)


def run_releases(  # noqa: PLR0913, PLR0917
    plan: Plan,
    probes: Mapping[str, Probe],
    ws: Workspace,
    states: Mapping[str, PackageState],
    git: GitPort,
    gh: GhPort,
    pypi: PyPIPort,
    *,
    wait: bool = True,
    on_progress: Callable[[str, str], None] | None = None,
    default_branch: str = DEFAULT_BRANCH,
    workers: int = 8,
) -> list[ReleaseResult]:
    """Do the probed action for every runnable package of ``plan``, in parallel."""
    notify = on_progress or (lambda _name, _msg: None)

    def run(item: PackagePlan) -> ReleaseResult:
        pkg, probe, since = ws.packages[item.name], probes[item.name], states[item.name].last_tag
        progress = lambda msg: notify(item.name, msg)  # noqa: E731
        if probe.action is Action.OPEN_PR:
            result = open_release_pr(item, pkg, git, gh, since=since, default_branch=default_branch, progress=progress)
            if result.outcome is Outcome.NOTHING_TO_COMMIT:
                result = publish_from_branch(item, pkg, git, gh, pypi, since=since, wait=wait, progress=progress,
                                             default_branch=default_branch)  # fmt: skip
        elif probe.action is Action.PUBLISH:
            result = publish_release(
                item, pkg, gh, pypi, pr_url=probe.pr_url or '', since=since, wait=wait, progress=progress
            )
        elif probe.action is Action.WAIT_MERGE:
            result = ReleaseResult(item.name, Outcome.ALREADY_OPEN, 'merge it, then run `release` again', probe.pr_url)
        elif probe.action is Action.RELEASED:
            result = ReleaseResult(item.name, Outcome.ALREADY_RELEASED, item.tag, probe.pr_url)
        else:
            result = ReleaseResult(item.name, Outcome.ERROR, probe.detail)
        notify(item.name, str(result.outcome))
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run, plan.runnable_now))
