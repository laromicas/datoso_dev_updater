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

    def _run(self, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        argv = [self.executable, *args]
        try:
            return self.runner(argv, cwd=repo, capture_output=True, text=True, timeout=self.timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            msg = f'gh {args[0]} {args[1]} in {repo}: {exc}'
            raise GhError(msg) from exc

    def _out(self, repo: Path, *args: str) -> str:
        proc = self._run(repo, *args)
        if proc.returncode != 0:
            msg = f'gh {args[0]} {args[1]} in {repo}: {(proc.stderr or proc.stdout).strip()}'
            raise GhError(msg)
        return proc.stdout.strip()

    def pr_for_branch(self, repo: Path, head: str, *, state: str = 'open') -> str | None:
        """URL of the PR in ``state`` (open, merged, …) whose head is ``head``, or ``None``."""
        url = self._out(repo, 'pr', 'list', '--head', head, '--state', state, '--json', 'url', '--jq', '.[0].url // ""')
        return url or None

    def pr_create(self, repo: Path, *, base: str, head: str, title: str, body: str) -> str:
        """Open a PR and return its URL (the last line gh prints)."""
        out = self._out(repo, 'pr', 'create', '--base', base, '--head', head, '--title', title, '--body', body)
        return out.splitlines()[-1].strip()

    def pr_merge_commit(self, repo: Path, pr_url: str) -> str:
        """SHA of the commit a merged PR landed as on the base branch."""
        return self._out(repo, 'pr', 'view', pr_url, '--json', 'mergeCommit', '--jq', '.mergeCommit.oid')

    def release_url(self, repo: Path, tag: str) -> str | None:
        """URL of the GitHub Release for ``tag``, or ``None`` when there is none."""
        proc = self._run(repo, 'release', 'view', tag, '--json', 'url', '--jq', '.url')
        if proc.returncode == 0:
            return proc.stdout.strip() or None
        error = (proc.stderr or proc.stdout).strip()
        if 'release not found' in error:
            return None
        msg = f'gh release view in {repo}: {error}'
        raise GhError(msg)

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
        """Create the tag and publish its GitHub Release with generated notes; return the release URL."""
        args = ['release', 'create', tag, '--target', target, '--title', title, '--generate-notes']
        if notes_start_tag:
            args += ['--notes-start-tag', notes_start_tag]
        if prerelease:
            args.append('--prerelease')
        return self._out(repo, *args).splitlines()[-1].strip()
