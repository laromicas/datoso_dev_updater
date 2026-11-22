"""In-memory fakes for the ports (no git, no network)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from datoso_dev.ports import GhError, GitError


@dataclass
class FakeRepo:
    branch: str = 'master'
    changed: list[str] = field(default_factory=list)
    ahead: int = 0
    behind: int = 0
    has_upstream: bool = True
    tags: list[str] = field(default_factory=list)
    commits_since: dict[str | None, int] = field(default_factory=dict)  # since-ref -> count
    branches: set[str] = field(default_factory=lambda: {'master'})
    fail_on: set[str] = field(default_factory=set)  # method names that raise GitError


class FakeGit:
    """Records calls; behaves according to per-repo FakeRepo state."""

    def __init__(self, repos: dict[Path, FakeRepo] | None = None) -> None:
        self.repos: dict[Path, FakeRepo] = repos or {}
        self.calls: list[tuple[str, Path]] = []
        self.commits: list[tuple[Path, list[str], str]] = []
        self.pushes: list[tuple[Path, str, bool]] = []

    def _repo(self, method: str, repo: Path) -> FakeRepo:
        self.calls.append((method, repo))
        state = self.repos[repo]
        if method in state.fail_on:
            msg = f'{method} failed in {repo}'
            raise GitError(msg)
        return state

    def fetch(self, repo: Path) -> None:
        self._repo('fetch', repo)

    def current_branch(self, repo: Path) -> str:
        return self._repo('current_branch', repo).branch

    def changed_files(self, repo: Path) -> list[str]:
        return list(self._repo('changed_files', repo).changed)

    def ahead_behind(self, repo: Path) -> tuple[int, int] | None:
        state = self._repo('ahead_behind', repo)
        return (state.ahead, state.behind) if state.has_upstream else None

    def tags(self, repo: Path) -> list[str]:
        return list(self._repo('tags', repo).tags)

    def count_commits(self, repo: Path, since: str | None) -> int:
        return self._repo('count_commits', repo).commits_since.get(since, 0)

    def switch(self, repo: Path, branch: str) -> None:
        state = self._repo('switch', repo)
        if branch not in state.branches:
            msg = f'no branch {branch}'
            raise GitError(msg)
        state.branch = branch

    def pull_rebase(self, repo: Path) -> None:
        state = self._repo('pull_rebase', repo)
        state.behind = 0

    def create_branch(self, repo: Path, branch: str) -> None:
        state = self._repo('create_branch', repo)
        state.branches.add(branch)
        state.branch = branch

    def commit_paths(self, repo: Path, paths: list[str], message: str) -> None:
        self._repo('commit_paths', repo)
        self.commits.append((repo, list(paths), message))

    def push(self, repo: Path, branch: str, *, force: bool = False) -> None:
        self._repo('push', repo)
        self.pushes.append((repo, branch, force))

    def called(self, method: str) -> list[Path]:
        return [repo for name, repo in self.calls if name == method]


class FakePyPI:
    def __init__(self, versions: dict[str, str | None] | None = None) -> None:
        self.versions = versions or {}
        self.queried: list[str] = []

    def latest_version(self, dist_name: str) -> str | None:
        self.queried.append(dist_name)
        return self.versions.get(dist_name)


class FakeGh:
    """In-memory GitHub: open PRs keyed by (repo, head branch)."""

    def __init__(self, open_prs: dict[tuple[Path, str], str] | None = None, *, fail: bool = False) -> None:
        self.open_prs = open_prs or {}
        self.created: list[dict[str, object]] = []
        self.fail = fail

    def pr_for_branch(self, repo: Path, head: str) -> str | None:
        return self.open_prs.get((repo, head))

    def pr_create(self, repo: Path, *, base: str, head: str, title: str, body: str) -> str:
        if self.fail:
            msg = f'gh pr create failed in {repo}'
            raise GhError(msg)
        url = f'https://github.com/owner/{repo.name}/pull/{len(self.created) + 1}'
        self.created.append({'repo': repo, 'base': base, 'head': head, 'title': title, 'body': body, 'url': url})
        self.open_prs[(repo, head)] = url
        return url
