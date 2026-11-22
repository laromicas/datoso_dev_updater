"""Interfaces to the outside world (git, PyPI).

Real implementations live in ``datoso_dev.io``; tests inject in-memory fakes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class GitError(Exception):
    """A git command failed."""


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


class PyPIPort(Protocol):
    """Read-only PyPI queries."""

    def latest_version(self, dist_name: str) -> str | None:
        """Latest released version string, or ``None`` if the project isn't on PyPI."""
