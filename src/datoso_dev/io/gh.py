"""GhPort implementation using the GitHub CLI (``gh``), which must be installed and logged in."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from datoso_dev.ports import Checks, GhError

Runner = Callable[..., subprocess.CompletedProcess[str]]
PENDING_EXIT = 8  # gh pr checks: some checks are still running


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

    def pr_create(self, repo: Path, *, base: str, head: str, title: str, body: str, draft: bool = False) -> str:  # noqa: PLR0913
        """Open a PR (a draft when ``draft``) and return its URL (the last line gh prints)."""
        args = ['pr', 'create', '--base', base, '--head', head, '--title', title, '--body', body]
        out = self._out(repo, *args, *(['--draft'] if draft else []))
        return out.splitlines()[-1].strip()

    def pr_body(self, repo: Path, pr_url: str) -> str:
        """Return the description of a PR."""
        return self._out(repo, 'pr', 'view', pr_url, '--json', 'body', '--jq', '.body')

    def pr_edit_body(self, repo: Path, pr_url: str, body: str) -> None:
        """Replace the description of a PR."""
        self._out(repo, 'pr', 'edit', pr_url, '--body', body)

    def pr_is_draft(self, repo: Path, pr_url: str) -> bool:
        """Whether a PR is a draft."""
        return self._out(repo, 'pr', 'view', pr_url, '--json', 'isDraft', '--jq', '.isDraft') == 'true'

    def pr_ready(self, repo: Path, pr_url: str) -> None:
        """Mark a draft PR ready for review."""
        self._out(repo, 'pr', 'ready', pr_url)

    def pr_merge_commit(self, repo: Path, pr_url: str) -> str:
        """SHA of the commit a merged PR landed as on the base branch."""
        return self._out(repo, 'pr', 'view', pr_url, '--json', 'mergeCommit', '--jq', '.mergeCommit.oid')

    def pr_checks(self, repo: Path, pr_url: str) -> tuple[Checks, str]:
        """CI state of a PR (``gh pr checks`` exits 0 pass, 8 pending, 1 failed) and the failing checks."""
        proc = self._run(repo, 'pr', 'checks', pr_url)
        if proc.returncode == 0:
            return Checks.PASSED, ''
        if proc.returncode == PENDING_EXIT:
            return Checks.PENDING, ''
        output = (proc.stderr or '') + (proc.stdout or '')
        if 'no checks reported' in output:
            return Checks.NONE, ''
        failed = [line.split('\t') for line in proc.stdout.splitlines() if '\tfail\t' in line]
        if failed:
            return Checks.FAILED, '; '.join(f'{cols[0]}: {cols[3]}' for cols in failed if len(cols) > 3)  # noqa: PLR2004
        msg = f'gh pr checks in {repo}: {output.strip()}'
        raise GhError(msg)

    def pr_merge(self, repo: Path, pr_url: str) -> None:
        """Squash-merge a PR (subject: its title + number) and delete its branch."""
        self._out(repo, 'pr', 'merge', pr_url, '--squash', '--delete-branch')

    def release_notes(self, repo: Path, tag: str) -> str:
        """Body of the GitHub Release for ``tag``."""
        return self._out(repo, 'release', 'view', tag, '--json', 'body', '--jq', '.body')

    def release_edit_notes(self, repo: Path, tag: str, notes_file: Path) -> None:
        """Replace the body of the GitHub Release for ``tag`` (does not re-trigger the PyPI upload)."""
        self._out(repo, 'release', 'edit', tag, '--notes-file', str(notes_file))

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
