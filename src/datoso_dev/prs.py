"""`datoso-dev prs`: list the open PRs of every repository, and merge the picked ones that you opened yourself."""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from datoso_dev.ports import Checks, GhError, GhPort, GitError, GitPort, PrInfo
from datoso_dev.state import DEFAULT_BRANCH


@dataclass(frozen=True)
class OpenPr:
    """An open PR of one repository, and why it can't be merged (empty: it can)."""

    repo: Path
    pr: PrInfo
    blocker: str = ''
    merged: bool = False
    error: str = ''

    @property
    def mergeable(self) -> bool:
        """Whether ``--merge`` may pick it."""
        return not self.blocker

    @property
    def green(self) -> bool:
        """Mergeable with passing (or no) CI: picked by default."""
        return self.mergeable and self.pr.checks in {Checks.PASSED, Checks.NONE}


def blocker(pr: PrInfo, me: str) -> str:
    """Why ``pr`` can't be merged by ``me``: someone else's, draft or conflicting (empty when it can)."""
    if pr.author != me:
        return f'opened by {pr.author or "unknown"}, not you'
    if pr.draft:
        return 'draft'
    if pr.mergeable == 'CONFLICTING':
        return 'conflicts with the base branch'
    return ''


def list_open(paths: Iterable[Path], gh: GhPort, me: str, *, workers: int = 8) -> tuple[list[OpenPr], list[str]]:
    """Open PRs of every repository (in order) and the repos that couldn't be listed."""

    def one(repo: Path) -> tuple[list[OpenPr], str]:
        try:
            return [OpenPr(repo, pr, blocker(pr, me)) for pr in gh.pr_list(repo)], ''
        except GhError as exc:
            return [], f'{repo.name}: {exc}'

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, paths))
    return [pr for prs, _ in results for pr in prs], [error for _, error in results if error]


def merge(prs: Iterable[OpenPr], gh: GhPort, me: str) -> list[OpenPr]:
    """Squash-merge each PR (deleting its branch), re-reading it from GitHub first: still open, still yours."""
    results = []
    for item in prs:
        try:
            fresh = next((pr for pr in gh.pr_list(item.repo) if pr.number == item.pr.number), None)
            reason = 'no longer open' if fresh is None else blocker(fresh, me)
            if reason:
                results.append(replace(item, blocker=reason))
                continue
            gh.pr_merge(item.repo, item.pr.url)
            results.append(replace(item, merged=True))
        except GhError as exc:
            results.append(replace(item, error=str(exc)))
    return results


def branch_prs(  # noqa: PLR0913
    paths: Iterable[Path], git: GitPort, gh: GhPort, me: str, *, default_branch: str = DEFAULT_BRANCH, workers: int = 8
) -> dict[Path, PrInfo]:
    """Repos checked out on a branch with an open PR of yours (only yours), mapped to that PR."""

    def one(repo: Path) -> tuple[Path, PrInfo | None]:
        try:
            branch = git.current_branch(repo)
            if branch in {default_branch, 'HEAD'}:
                return repo, None
            return repo, next((pr for pr in gh.pr_list(repo) if pr.branch == branch and pr.author == me), None)
        except (GitError, GhError):
            return repo, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return {repo: pr for repo, pr in pool.map(one, paths) if pr is not None}
