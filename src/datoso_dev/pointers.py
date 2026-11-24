"""`datoso-dev pointers`: update the submodule pointers the workspace records, when they are safe to record.

It never adds anything: a submodule the workspace doesn't record yet, or any untracked file, is added by hand.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from datoso_dev.ports import GitError, GitPort, PrInfo
from datoso_dev.state import DEFAULT_BRANCH
from datoso_dev.workspace import Workspace

MAX_SUBJECTS = 3
NOT_RECORDED = 'not recorded in the workspace yet: add it yourself (git submodule add / git add)'


@dataclass(frozen=True)
class Pointer:
    """A submodule whose checked-out commit differs from the one the workspace records."""

    path: Path
    old: str | None = None
    new: str | None = None
    commits: tuple[str, ...] = ()  # subjects old..new, newest first
    blocker: str = ''

    @property
    def name(self) -> str:
        """Directory name of the submodule."""
        return self.path.name


def _blocker(git: GitPort, repo: Path, default_branch: str, pr: PrInfo | None = None) -> str:
    """Why the submodule's commit shouldn't be recorded yet (empty when it can)."""
    if git.changed_files(repo):
        return 'uncommitted changes (commit them in the repo first)'
    branch = git.current_branch(repo)
    if branch != default_branch:
        why = f'your PR #{pr.number} is still open' if pr is not None else f'not {default_branch}'
        return f'on {branch}: {why} (merge it first)'
    counts = git.ahead_behind(repo)
    if counts is None:
        return 'no upstream'
    ahead, behind = counts
    if ahead:
        return f'{ahead} unpushed commit(s)'
    if behind:
        return f'behind origin by {behind} (pull first)'
    return ''


def plan_pointers(
    ws: Workspace,
    git: GitPort,
    *,
    open_prs: Mapping[Path, PrInfo] | None = None,
    default_branch: str = DEFAULT_BRANCH,
) -> list[Pointer]:
    """Every moved pointer of the workspace, with what it adds and whether it can be committed."""
    open_prs = open_prs or {}
    changed = set(git.changed_files(ws.root))
    pointers = []
    for repo in ws.submodule_paths:
        rel = str(repo.relative_to(ws.root))
        if rel not in changed:
            continue
        try:
            old = git.committed_pointer(ws.root, rel)
            if old is None:  # only update recorded pointers: a new submodule is added by hand
                pointers.append(Pointer(repo, blocker=NOT_RECORDED))
                continue
            new = git.rev_parse(repo, 'HEAD')
            if old == new:
                pointers.append(Pointer(repo, old, new, blocker='uncommitted changes (commit them in the repo first)'))
                continue
            try:
                commits = tuple(git.log_subjects(repo, old, 'HEAD'))
            except GitError:
                commits = ()  # the old commit is gone (rewritten history): still recordable
            pointers.append(Pointer(repo, old, new, commits, _blocker(git, repo, default_branch, open_prs.get(repo))))
        except GitError as exc:
            pointers.append(Pointer(repo, blocker=str(exc)))
    return pointers


def commit_message(pointers: Iterable[Pointer]) -> str:
    """``Pointers: a, b`` plus one line per repo with its newest commit subjects."""
    pointers = list(pointers)
    lines = [f'Pointers: {", ".join(pointer.name for pointer in pointers)}', '']
    for pointer in pointers:
        shown = '; '.join(pointer.commits[:MAX_SUBJECTS])
        more = f' (+{len(pointer.commits) - MAX_SUBJECTS} more)' if len(pointer.commits) > MAX_SUBJECTS else ''
        lines.append(f'- {pointer.name}: {shown or "moved"}{more}')
    return '\n'.join(lines)


def workspace_blocker(ws: Workspace, git: GitPort, *, default_branch: str = DEFAULT_BRANCH) -> str:
    """Why the workspace repo itself can't take the commit (empty when it can)."""
    branch = git.current_branch(ws.root)
    if branch != default_branch:
        return f'the workspace is on {branch}, not {default_branch}'
    counts = git.ahead_behind(ws.root)
    if counts is None:
        return 'the workspace has no upstream'
    if counts[1]:
        return f'the workspace is behind origin by {counts[1]} (pull it first)'
    return ''


def commit_pointers(ws: Workspace, git: GitPort, pointers: Iterable[Pointer], *, branch: str = DEFAULT_BRANCH) -> str:
    """Commit only these already-recorded submodule paths (never a new one) and push; return the message."""
    pointers = [pointer for pointer in pointers if not pointer.blocker and pointer.old]
    message = commit_message(pointers)
    git.commit_paths(ws.root, [str(pointer.path.relative_to(ws.root)) for pointer in pointers], message)
    git.push(ws.root, branch)
    return message
