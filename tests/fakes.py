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
    files: dict[tuple[str, str], str] = field(default_factory=dict)  # (ref, path) -> content
    subjects: list[str] = field(default_factory=list)  # commit subjects returned by log_subjects


class FakeGit:
    """Records calls; behaves according to per-repo FakeRepo state."""

    def __init__(self, repos: dict[Path, FakeRepo] | None = None) -> None:
        self.repos: dict[Path, FakeRepo] = repos or {}
        self.calls: list[tuple[str, Path]] = []
        self.commits: list[tuple[Path, list[str], str]] = []
        self.pushes: list[tuple[Path, str, bool]] = []
        self.branch_starts: list[tuple[Path, str, str | None]] = []

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

    def create_branch(self, repo: Path, branch: str, start: str | None = None) -> None:
        state = self._repo('create_branch', repo)
        self.branch_starts.append((repo, branch, start))
        state.branches.add(branch)
        state.branch = branch

    def commit_paths(self, repo: Path, paths: list[str], message: str) -> None:
        self._repo('commit_paths', repo)
        self.commits.append((repo, list(paths), message))

    def push(self, repo: Path, branch: str, *, force: bool = False) -> None:
        self._repo('push', repo)
        self.pushes.append((repo, branch, force))

    def log_subjects(self, repo: Path, since: str | None, rev: str) -> list[str]:
        return list(self._repo('log_subjects', repo).subjects)

    def show_file(self, repo: Path, ref: str, path: str) -> str | None:
        return self._repo('show_file', repo).files.get((ref, path))

    def called(self, method: str) -> list[Path]:
        return [repo for name, repo in self.calls if name == method]


class FakePyPI:
    def __init__(self, versions: dict[str, str | None] | None = None) -> None:
        self.versions = versions or {}
        self.queried: list[str] = []
        self.appears_after: dict[tuple[str, str], int] = {}  # (dist, version) -> polls before it is listed
        self.polls: list[tuple[str, str]] = []

    def has_version(self, dist_name: str, version: str) -> bool:
        self.polls.append((dist_name, version))
        key = (dist_name, version)
        if key in self.appears_after:
            return self.polls.count(key) > self.appears_after[key]
        return self.versions.get(dist_name) == version

    def latest_version(self, dist_name: str) -> str | None:
        self.queried.append(dist_name)
        return self.versions.get(dist_name)


class FakeGh:
    """In-memory GitHub: open PRs keyed by (repo, head branch)."""

    def __init__(self, open_prs: dict[tuple[Path, str], str] | None = None, *, fail: bool = False) -> None:
        self.open_prs = open_prs or {}
        self.merged_prs: dict[tuple[Path, str], str] = {}
        self.releases: dict[tuple[Path, str], str] = {}  # (repo, tag) -> release URL
        self.releases_created: list[dict[str, object]] = []
        self.created: list[dict[str, object]] = []
        self.fail = fail

    def pr_for_branch(self, repo: Path, head: str, *, state: str = 'open') -> str | None:
        return (self.open_prs if state == 'open' else self.merged_prs).get((repo, head))

    def pr_create(self, repo: Path, *, base: str, head: str, title: str, body: str) -> str:
        if self.fail:
            msg = f'gh pr create failed in {repo}'
            raise GhError(msg)
        url = f'https://github.com/owner/{repo.name}/pull/{len(self.created) + 1}'
        self.created.append({'repo': repo, 'base': base, 'head': head, 'title': title, 'body': body, 'url': url})
        self.open_prs[(repo, head)] = url
        return url

    def pr_merge_commit(self, repo: Path, pr_url: str) -> str:
        return f'sha-of-{pr_url.rsplit("/", 1)[-1]}'

    def release_url(self, repo: Path, tag: str) -> str | None:
        return self.releases.get((repo, tag))

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
        if self.fail:
            msg = f'gh release create failed in {repo}'
            raise GhError(msg)
        url = f'https://github.com/owner/{repo.name}/releases/tag/{tag}'
        self.releases_created.append(
            {'repo': repo, 'tag': tag, 'target': target, 'title': title, 'notes_start_tag': notes_start_tag,
             'prerelease': prerelease}
        )  # fmt: skip
        self.releases[(repo, tag)] = url
        return url


class FakePrompter:
    """Replays scripted answers and records every question.

    Answers for ``select``/``checkbox`` may be callables receiving the offered options, so tests can assert on them.
    """

    def __init__(self, answers: list[object]) -> None:
        self.answers = list(answers)
        self.asked: list[tuple[str, str, list]] = []  # (kind, message, options)

    def _next(self, kind: str, message: str, options: list) -> object:
        self.asked.append((kind, message, options))
        if not self.answers:
            msg = f'unexpected {kind} prompt: {message}'
            raise AssertionError(msg)
        answer = self.answers.pop(0)
        return answer(options) if callable(answer) else answer

    def select(self, message: str, options):
        return self._next('select', message, list(options))

    def checkbox(self, message: str, options):
        return self._next('checkbox', message, list(options))

    def confirm(self, message: str, *, default: bool = False):
        return self._next('confirm', message, [])
