"""`datoso-dev sync`: bring every repository to an up-to-date default branch, safely."""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from datoso_dev.ports import GitError, GitPort
from datoso_dev.state import DEFAULT_BRANCH


class Outcome(StrEnum):
    """Result of syncing one repository."""

    UPDATED = 'updated'
    UP_TO_DATE = 'up to date'
    SKIPPED = 'skipped'
    ERROR = 'error'


@dataclass(frozen=True)
class SyncResult:
    """What happened (or, in dry-run, would happen) to one repository."""

    path: Path
    outcome: Outcome
    detail: str = ''
    switched_from: str | None = None
    dry_run: bool = False


def sync_repo(path: Path, git: GitPort, *, branch: str = DEFAULT_BRANCH, dry_run: bool = False) -> SyncResult:
    """Fetch; if the tree is clean, switch to ``branch`` and ``pull --rebase`` when behind.

    Dirty repositories are never touched. In dry-run only ``fetch`` runs (it doesn't change the working tree).
    """

    def result(outcome: Outcome, detail: str = '', switched_from: str | None = None) -> SyncResult:
        return SyncResult(path, outcome, detail, switched_from, dry_run)

    try:
        git.fetch(path)
        changed = git.changed_files(path)
        if changed:
            return result(Outcome.SKIPPED, f'{len(changed)} uncommitted file(s)')

        current = git.current_branch(path)
        switched_from = current if current != branch else None
        if switched_from and not dry_run:
            git.switch(path, branch)

        ahead_behind = git.ahead_behind(path)
        if ahead_behind is None:
            return result(Outcome.SKIPPED, 'no upstream', switched_from)
        ahead, behind = ahead_behind

        notes = [f'{ahead} unpushed commit(s)'] if ahead else []
        if behind:
            if not dry_run:
                git.pull_rebase(path)
            return result(Outcome.UPDATED, ', '.join([f'pulled {behind} commit(s)', *notes]), switched_from)
        outcome = Outcome.UPDATED if switched_from else Outcome.UP_TO_DATE
        return result(outcome, ', '.join(notes), switched_from)
    except (GitError, OSError) as exc:
        return result(Outcome.ERROR, str(exc))


def sync_all(
    paths: Iterable[Path], git: GitPort, *, branch: str = DEFAULT_BRANCH, dry_run: bool = False, workers: int = 8
) -> list[SyncResult]:
    """Sync many repositories in parallel, preserving order."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda p: sync_repo(p, git, branch=branch, dry_run=dry_run), paths))
