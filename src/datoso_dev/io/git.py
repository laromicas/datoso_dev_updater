"""GitPort implementation using the ``git`` executable."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from datoso_dev.ports import GitError


class Git:
    """Runs git commands in a repository directory."""

    def __init__(self, executable: str = 'git', timeout: float = 120) -> None:
        """Configure the git executable and per-command timeout (seconds)."""
        self.executable = executable
        self.timeout = timeout

    def _run(self, repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'LC_ALL': 'C'}
        try:
            proc = subprocess.run(  # noqa: S603
                [self.executable, *args],
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            msg = f'git {" ".join(args)} in {repo}: {exc}'
            raise GitError(msg) from exc
        if check and proc.returncode != 0:
            msg = f'git {" ".join(args)} in {repo}: {(proc.stderr or proc.stdout).strip()}'
            raise GitError(msg)
        return proc

    def _out(self, repo: Path, *args: str) -> str:
        return self._run(repo, *args).stdout.strip()

    def _has_commits(self, repo: Path) -> bool:
        return self._run(repo, 'rev-parse', '--verify', '--quiet', 'HEAD', check=False).returncode == 0

    def fetch(self, repo: Path) -> None:
        """Fetch the default remote, including tags."""
        self._run(repo, 'fetch', '--quiet', '--tags', '--prune')

    def current_branch(self, repo: Path) -> str:
        """Return the current branch, or ``'HEAD'`` when detached."""
        proc = self._run(repo, 'symbolic-ref', '--quiet', '--short', 'HEAD', check=False)
        return proc.stdout.strip() if proc.returncode == 0 else 'HEAD'

    def changed_files(self, repo: Path) -> list[str]:
        """Return modified, staged and untracked (not ignored) files."""
        out = self._run(repo, 'status', '--porcelain=v1', '--untracked-files=all').stdout
        return [line[3:] for line in out.splitlines() if line.strip()]

    def ahead_behind(self, repo: Path) -> tuple[int, int] | None:
        """``(ahead, behind)`` relative to the upstream, or ``None`` when there is no upstream."""
        upstream = self._run(repo, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}', check=False)
        if upstream.returncode != 0 or not self._has_commits(repo):
            return None
        behind, ahead = self._out(repo, 'rev-list', '--left-right', '--count', '@{upstream}...HEAD').split()
        return int(ahead), int(behind)

    def tags(self, repo: Path) -> list[str]:
        """All tag names."""
        return [t for t in self._out(repo, 'tag', '--list').splitlines() if t]

    def count_commits(self, repo: Path, since: str | None) -> int:
        """Count commits in ``since..HEAD`` (or all commits when ``since`` is None)."""
        if not self._has_commits(repo):
            return 0
        rev = f'{since}..HEAD' if since else 'HEAD'
        return int(self._out(repo, 'rev-list', '--count', rev))

    def switch(self, repo: Path, branch: str) -> None:
        """Switch to an existing local branch."""
        self._run(repo, 'switch', '--quiet', branch)

    def pull_rebase(self, repo: Path) -> None:
        """Pull the upstream of the current branch with rebase."""
        self._run(repo, 'pull', '--rebase', '--quiet')
