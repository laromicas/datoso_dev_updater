"""`datoso-dev ci`: one managed CI workflow, rendered per package and rolled out through PRs.

Core (``datoso``) keeps its own pytest workflow and is never a target.
"""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from importlib.resources import files
from pathlib import Path

from datoso_dev.ports import GhError, GhPort, GitError, GitPort
from datoso_dev.state import DEFAULT_BRANCH
from datoso_dev.workspace import CORE, Package, Workspace, WorkspaceError

WORKFLOW_PATH = Path('.github/workflows/ci.yml')
CI_BRANCH = 'datoso-dev/ci'
SEED_PREFIX = 'datoso_seed_'

_SEED_STEPS = """\
      - name: datoso doctor
        run: datoso doctor {seed}
"""

_PR_BODY = """\
Adds or updates the shared CI workflow managed by `datoso-dev ci sync`.

On every pull request it installs the package (dependency floors resolve from PyPI), runs `ruff check`, imports every
module, runs `datoso doctor` for seeds, and runs `pytest` when a `tests/` directory exists.

The template lives in `datoso_dev_updater/src/datoso_dev/templates/ci.yml`; edit it there, not in this repository.
"""


class CiStatus(StrEnum):
    """Whether a package's workflow matches the rendered template."""

    UP_TO_DATE = 'up to date'
    MISSING = 'missing'
    OUTDATED = 'outdated'


class CiOutcome(StrEnum):
    """Result of syncing the workflow into one repository."""

    UP_TO_DATE = 'up to date'
    OPENED = 'PR opened'
    UPDATED = 'PR updated'
    SKIPPED = 'skipped'
    ERROR = 'error'


@dataclass(frozen=True)
class CiResult:
    """What happened (or, in dry-run, would happen) to one package."""

    package: Package
    outcome: CiOutcome
    detail: str = ''
    pr_url: str | None = None
    dry_run: bool = False


def template_text() -> str:
    """Return the packaged workflow template."""
    return (files('datoso_dev') / 'templates' / 'ci.yml').read_text(encoding='utf-8')


def render_ci(pkg: Package, template: str | None = None) -> str:
    """Render the workflow for ``pkg``: seeds also get a ``datoso doctor`` step."""
    text = template_text() if template is None else template
    seed_steps = _SEED_STEPS.format(seed=pkg.name.removeprefix(SEED_PREFIX)) if pkg.name.startswith(SEED_PREFIX) else ''
    return text.replace('@PACKAGE@', pkg.name).replace('@SEED_STEPS@', seed_steps)


def _resolve(ws: Workspace, name: str) -> str:
    for candidate in (name, f'{SEED_PREFIX}{name}'):
        if candidate in ws.packages:
            return candidate
    msg = f'unknown package: {name}'
    raise WorkspaceError(msg)


def ci_targets(ws: Workspace, *, include_skipped: bool = False, only: Iterable[str] | None = None) -> list[Package]:
    """Return the packages that get the managed workflow, in workspace order (never core)."""
    if only is not None:
        selected = {_resolve(ws, name) for name in only}
        if CORE in selected:
            msg = f'{CORE} keeps its own test workflow and is not a ci target'
            raise WorkspaceError(msg)
        return [pkg for name, pkg in ws.packages.items() if name in selected]
    return [pkg for name, pkg in ws.packages.items() if name != CORE and (include_skipped or not pkg.skip)]


def ci_status(pkg: Package) -> CiStatus:
    """Compare the workflow in the working tree with the rendered template."""
    workflow = pkg.path / WORKFLOW_PATH
    if not workflow.is_file():
        return CiStatus.MISSING
    return CiStatus.UP_TO_DATE if workflow.read_text(encoding='utf-8') == render_ci(pkg) else CiStatus.OUTDATED


def _unsafe(git: GitPort, repo: Path, default_branch: str) -> str | None:
    """Why the repo can't be used as a clean base for the CI branch, or ``None``."""
    if git.changed_files(repo):
        return 'uncommitted changes'
    branch = git.current_branch(repo)
    if branch != default_branch:
        return f'on {branch}, not {default_branch}'
    counts = git.ahead_behind(repo)
    if counts is None:
        return 'no upstream'
    ahead, behind = counts
    if behind:
        return f'behind by {behind} (run datoso-dev sync)'
    if ahead:
        return f'{ahead} unpushed commit(s)'
    return None


def ci_sync(
    pkg: Package,
    git: GitPort,
    gh: GhPort,
    *,
    dry_run: bool = False,
    default_branch: str = DEFAULT_BRANCH,
) -> CiResult:
    """Put the rendered workflow on ``CI_BRANCH`` and make sure a PR is open for it."""
    repo = pkg.path
    try:
        git.fetch(repo)
        reason = _unsafe(git, repo, default_branch)
        if reason:
            return CiResult(pkg, CiOutcome.SKIPPED, reason, dry_run=dry_run)
        status = ci_status(pkg)
        if status is CiStatus.UP_TO_DATE:
            return CiResult(pkg, CiOutcome.UP_TO_DATE, dry_run=dry_run)
        existing = gh.pr_for_branch(repo, CI_BRANCH)
        outcome = CiOutcome.UPDATED if existing else CiOutcome.OPENED
        verb = 'add' if status is CiStatus.MISSING else 'update'
        title = f'CI: {verb} datoso-dev managed workflow'
        if dry_run:
            return CiResult(pkg, outcome, f'workflow {status}', existing, dry_run=True)
        _commit_workflow(pkg, git, title, default_branch)
        url = existing or gh.pr_create(repo, base=default_branch, head=CI_BRANCH, title=title, body=_PR_BODY)
    except (GitError, GhError) as exc:
        return CiResult(pkg, CiOutcome.ERROR, str(exc), dry_run=dry_run)
    return CiResult(pkg, outcome, f'workflow {status}', url)


def _commit_workflow(pkg: Package, git: GitPort, message: str, default_branch: str) -> None:
    repo = pkg.path
    workflow = repo / WORKFLOW_PATH
    previous = workflow.read_text(encoding='utf-8') if workflow.is_file() else None
    git.create_branch(repo, CI_BRANCH)
    committed = False
    try:
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text(render_ci(pkg), encoding='utf-8')
        git.commit_paths(repo, [str(WORKFLOW_PATH)], message)
        committed = True
        git.push(repo, CI_BRANCH, force=True)
    finally:
        if not committed:  # don't carry an uncommitted workflow back onto the default branch
            if previous is None:
                workflow.unlink(missing_ok=True)
            else:
                workflow.write_text(previous, encoding='utf-8')
        git.switch(repo, default_branch)


def ci_sync_all(
    packages: Iterable[Package],
    git: GitPort,
    gh: GhPort,
    *,
    dry_run: bool = False,
    workers: int = 8,
) -> list[CiResult]:
    """Run :func:`ci_sync` for each package (one repo per thread), in input order."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda pkg: ci_sync(pkg, git, gh, dry_run=dry_run), packages))
