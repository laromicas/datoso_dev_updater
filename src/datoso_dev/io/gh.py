"""GhPort implementation using the GitHub CLI (``gh``), which must be installed and logged in."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from datoso_dev.ports import GhError

Runner = Callable[..., subprocess.CompletedProcess[str]]


class Gh:
    """Runs ``gh`` in a repository checkout (gh resolves the GitHub repo from its ``origin``)."""

    def __init__(self, executable: str = 'gh', timeout: float = 120, runner: Runner = subprocess.run) -> None:
        """Configure the executable, per-command timeout (seconds) and the subprocess runner (for tests)."""
        self.executable = executable
        self.timeout = timeout
        self.runner = runner

    def _out(self, repo: Path, *args: str) -> str:
        argv = [self.executable, *args]
        try:
            proc = self.runner(argv, cwd=repo, capture_output=True, text=True, timeout=self.timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            msg = f'gh {args[0]} {args[1]} in {repo}: {exc}'
            raise GhError(msg) from exc
        if proc.returncode != 0:
            msg = f'gh {args[0]} {args[1]} in {repo}: {(proc.stderr or proc.stdout).strip()}'
            raise GhError(msg)
        return proc.stdout.strip()

    def pr_for_branch(self, repo: Path, head: str) -> str | None:
        """URL of the open PR whose head is ``head``, or ``None``."""
        url = self._out(
            repo, 'pr', 'list', '--head', head, '--state', 'open', '--json', 'url', '--jq', '.[0].url // ""'
        )
        return url or None

    def pr_create(self, repo: Path, *, base: str, head: str, title: str, body: str) -> str:
        """Open a PR and return its URL (the last line gh prints)."""
        out = self._out(repo, 'pr', 'create', '--base', base, '--head', head, '--title', title, '--body', body)
        return out.splitlines()[-1].strip()
