"""Interfaces to the outside world (git, GitHub, PyPI).

Real implementations live in ``datoso_dev.io``; tests inject in-memory fakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class GitError(Exception):
    """A git command failed."""


class GhError(Exception):
    """A GitHub CLI (``gh``) command failed."""


class GitPort(Protocol):
    """Git operations datoso-dev needs, per repository path."""

    def fetch(self, repo: Path) -> None:
        """Fetch from the default remote (tags included)."""

    def current_branch(self, repo: Path) -> str:
        """Return the current branch name, or ``'HEAD'`` when detached."""

    def changed_files(self, repo: Path) -> list[str]:
        """Return modified, staged and untracked files (respecting .gitignore)."""

    def ahead_behind(self, repo: Path) -> tuple[int, int] | None:
        """Return commits ahead/behind the upstream of the current branch, or ``None`` without upstream."""

    def tags(self, repo: Path) -> list[str]:
        """All tag names."""

    def count_commits(self, repo: Path, since: str | None) -> int:
        """Count commits in ``since..HEAD`` (all of HEAD's history when ``since`` is None)."""

    def switch(self, repo: Path, branch: str) -> None:
        """Switch to an existing local branch."""

    def pull_rebase(self, repo: Path) -> None:
        """``git pull --rebase`` on the current branch."""

    def create_branch(self, repo: Path, branch: str, start: str | None = None) -> None:
        """Create (or reset) ``branch`` at ``start`` (default: the current commit) and switch to it."""

    def branch_exists(self, repo: Path, branch: str) -> bool:
        """Whether ``branch`` exists locally or on ``origin``."""

    def force_branch(self, repo: Path, branch: str, start: str) -> None:
        """Move the (not checked out) ``branch`` to ``start``."""

    def log_subjects(self, repo: Path, since: str | None, rev: str) -> list[str]:
        """Commit subjects in ``since..rev`` (all history of ``rev`` when ``since`` is None), newest first."""

    def commit_paths(self, repo: Path, paths: list[str], message: str) -> None:
        """Stage ``paths`` (relative to the repo) and commit them."""

    def push(self, repo: Path, branch: str, *, force: bool = False) -> None:
        """Push ``branch`` to ``origin`` and set its upstream."""

    def rev_parse(self, repo: Path, ref: str) -> str:
        """Commit SHA of ``ref`` (e.g. ``origin/master``)."""

    def show_file(self, repo: Path, ref: str, path: str) -> str | None:
        """Content of ``path`` at ``ref`` (e.g. ``origin/branch``), or ``None`` if the ref or file doesn't exist."""


class Checks(StrEnum):
    """CI state of a pull request."""

    PASSED = 'passed'
    FAILED = 'failed'
    PENDING = 'pending'
    NONE = 'none'  # no checks reported (yet)


@dataclass(frozen=True)
class PrInfo:
    """An open pull request, as listed by GitHub."""

    number: int
    title: str
    branch: str
    url: str
    author: str
    draft: bool = False
    mergeable: str = 'UNKNOWN'  # MERGEABLE, CONFLICTING or UNKNOWN
    checks: Checks = Checks.NONE


class GhPort(Protocol):
    """GitHub pull-request operations, run in a repository checkout."""

    def pr_for_branch(self, repo: Path, head: str, *, state: str = 'open') -> str | None:
        """URL of the PR in ``state`` (``open``, ``merged``) whose head is ``head``, or ``None``."""

    def pr_create(self, repo: Path, *, base: str, head: str, title: str, body: str, draft: bool = False) -> str:  # noqa: PLR0913
        """Open a PR (a draft when ``draft``) and return its URL."""

    def pr_body(self, repo: Path, pr_url: str) -> str:
        """Return the description of a PR."""

    def pr_edit_body(self, repo: Path, pr_url: str, body: str) -> None:
        """Replace the description of a PR."""

    def pr_is_draft(self, repo: Path, pr_url: str) -> bool:
        """Whether a PR is a draft."""

    def pr_ready(self, repo: Path, pr_url: str) -> None:
        """Mark a draft PR ready for review."""

    def pr_list(self, repo: Path) -> list[PrInfo]:
        """Open PRs of the repository."""

    def current_user(self, repo: Path) -> str:
        """Login of the account ``gh`` is authenticated as."""

    def pr_merge_commit(self, repo: Path, pr_url: str) -> str:
        """SHA of the commit a merged PR landed as."""

    def pr_checks(self, repo: Path, pr_url: str) -> tuple[Checks, str]:
        """CI state of a PR, plus the failing checks (``name: link``) when it failed."""

    def pr_merge(self, repo: Path, pr_url: str) -> None:
        """Squash-merge a PR and delete its branch."""

    def release_url(self, repo: Path, tag: str) -> str | None:
        """URL of the GitHub Release for ``tag``, or ``None``."""

    def release_notes(self, repo: Path, tag: str) -> str:
        """Body of the GitHub Release for ``tag``."""

    def release_edit_notes(self, repo: Path, tag: str, notes_file: Path) -> None:
        """Replace the body of the GitHub Release for ``tag``."""

    def release_create(  # noqa: PLR0913
        self,
        repo: Path,
        *,
        tag: str,
        target: str,
        title: str,
        notes_start_tag: str | None = None,
        prerelease: bool = False,
    ) -> str:
        """Create ``tag`` at ``target`` and publish its GitHub Release; return its URL."""


class PyPIPort(Protocol):
    """Read-only PyPI queries."""

    def latest_version(self, dist_name: str) -> str | None:
        """Latest released version string, or ``None`` if the project isn't on PyPI."""

    def has_version(self, dist_name: str, version: str) -> bool:
        """Whether ``version`` of the project is published."""
