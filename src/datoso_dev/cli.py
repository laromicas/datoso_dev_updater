"""`datoso-dev` command line interface."""

from __future__ import annotations

import json
import os
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console

from datoso_dev import __version__
from datoso_dev.ci import CiOutcome, ci_status, ci_sync_all, ci_targets
from datoso_dev.ci_menu import ci_menu
from datoso_dev.io.gh import Gh
from datoso_dev.io.git import Git
from datoso_dev.io.pypi import PyPI
from datoso_dev.planner import PlanError, plan_release, released_version
from datoso_dev.policy import violations
from datoso_dev.ports import GhPort, GitPort, PyPIPort
from datoso_dev.prompts import Prompter, QuestionaryPrompter
from datoso_dev.pyproject import set_floor, set_init_version, set_project_version
from datoso_dev.release import Outcome as ReleaseOutcome
from datoso_dev.release import open_release_prs
from datoso_dev.release_menu import choose_release, target_version
from datoso_dev.state import DEFAULT_BRANCH, PackageState, Status, collect_states
from datoso_dev.sync import Outcome, sync_all
from datoso_dev.ui import (
    ci_status_table,
    ci_sync_table,
    deps_table,
    plan_table,
    print_pr_urls,
    print_status,
    release_results_table,
    sync_table,
)
from datoso_dev.versioning import Part, VersionError, bump
from datoso_dev.workspace import Workspace, WorkspaceError, discover, find_workspace_root, resolve_name

app = typer.Typer(help='Release and maintenance tool for the datoso workspace.', no_args_is_help=True)
ci_app = typer.Typer(help='Managed CI workflow for the plugin and seeds. Without a subcommand: interactive menu.')
app.add_typer(ci_app, name='ci')
console = Console()

WORKSPACE_ENV = 'DATOSO_DEV_WORKSPACE'


# Factories are module-level so tests can replace them with fakes.
def make_git() -> GitPort:
    """Real git adapter."""
    return Git()


def make_pypi() -> PyPIPort:
    """Real PyPI adapter."""
    return PyPI()


def make_gh() -> GhPort:
    """Real GitHub CLI adapter."""
    return Gh()


def make_prompter() -> Prompter:
    """Real terminal prompts."""
    return QuestionaryPrompter()


def is_interactive() -> bool:
    """Whether we can prompt (stdin and stdout are terminals)."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def resolve_workspace(explicit: Path | None) -> Workspace:
    """Workspace from ``--workspace``, ``$DATOSO_DEV_WORKSPACE``, the cwd, or this tool's own location."""
    if explicit is None and os.environ.get(WORKSPACE_ENV):
        explicit = Path(os.environ[WORKSPACE_ENV])
    starts = [explicit] if explicit else [Path.cwd(), Path(__file__).resolve()]
    errors = []
    for start in starts:
        try:
            return discover(find_workspace_root(start.expanduser().resolve()))
        except WorkspaceError as exc:
            errors.append(str(exc))
    console.print(f'[bold red]Error:[/] {errors[0]}')
    raise typer.Exit(2)


WorkspaceOpt = Annotated[
    Path | None,
    typer.Option(
        '--workspace', '-w', help=f'Workspace root (default: auto-detect; env {WORKSPACE_ENV}).', show_default=False
    ),
]


def _version_callback(value: bool) -> None:
    if value:
        console.print(f'datoso-dev {__version__}')
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            '--version',
            callback=_version_callback,
            is_eager=True,
            help='Show version and exit.',
        ),
    ] = False,
) -> None:
    """Release and maintenance tool for the datoso workspace."""


def _state_json(state: PackageState) -> dict:
    return {
        'package': state.package.name,
        'dist_name': state.package.dist_name,
        'version': str(state.package.version),
        'last_tag': state.last_tag,
        'pypi_version': None if state.pypi_version is None else str(state.pypi_version),
        'commits_since_tag': state.commits_since_tag,
        'branch': state.branch,
        'ahead': state.ahead,
        'behind': state.behind,
        'changed_files': list(state.changed_files),
        'skip': state.package.skip,
        'release': state.package.release,
        'status': str(state.status),
        'issues': state.issues,
        'error': state.error,
    }


@app.command()
def status(
    workspace: WorkspaceOpt = None,
    fetch: Annotated[bool, typer.Option(help='git fetch every repo first (accurate ahead/behind).')] = True,
    offline: Annotated[bool, typer.Option('--offline', help='No fetch and no PyPI queries.')] = False,
    as_json: Annotated[bool, typer.Option('--json', help='Machine-readable output.')] = False,
) -> None:
    """Show every package: version, last tag, PyPI, unreleased commits, branch and issues."""
    ws = resolve_workspace(workspace)
    with console.status('Collecting repository state…', spinner='dots') if not as_json else nullcontext():
        states = collect_states(ws, make_git(), make_pypi(), fetch=fetch and not offline, query_pypi=not offline)
    if as_json:
        print(json.dumps([_state_json(s) for s in states], indent=2))
    else:
        print_status(console, states)
    if any(s.status is Status.ERROR for s in states):
        raise typer.Exit(1)


@app.command()
def sync(
    workspace: WorkspaceOpt = None,
    dry_run: Annotated[bool, typer.Option('--dry-run', help='Only fetch and report; change nothing.')] = False,
    branch: Annotated[str, typer.Option(help='Branch to switch to and update.')] = DEFAULT_BRANCH,
) -> None:
    """Fetch every repo; switch clean ones to the default branch and pull --rebase. Dirty repos are skipped."""
    ws = resolve_workspace(workspace)
    with console.status('Syncing repositories…', spinner='dots'):
        results = sync_all(ws.submodule_paths, make_git(), branch=branch, dry_run=dry_run)
    console.print(sync_table(results, dry_run=dry_run))
    if any(r.outcome is Outcome.ERROR for r in results):
        raise typer.Exit(1)


IncludeSkippedOpt = Annotated[
    bool, typer.Option('--include-skipped', help=r'Also target packages with \[tool.datoso-dev] skip = true.')
]


def _run_ci_menu(workspace: Path | None, *, include_skipped: bool) -> None:
    if not is_interactive():
        console.print('[bold red]Error:[/] not a terminal: pass package names or --all (and --yes) to `ci sync`.')
        raise typer.Exit(2)
    ws = resolve_workspace(workspace)
    code = ci_menu(ws, make_git(), make_gh(), make_prompter(), console, include_skipped=include_skipped)
    raise typer.Exit(code)


@ci_app.callback(invoke_without_command=True)
def ci_main(ctx: typer.Context, workspace: WorkspaceOpt = None, include_skipped: IncludeSkippedOpt = False) -> None:
    """Open the interactive menu when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        _run_ci_menu(workspace, include_skipped=include_skipped)


@ci_app.command('status')
def ci_status_command(workspace: WorkspaceOpt = None, include_skipped: IncludeSkippedOpt = False) -> None:
    """Compare each package's .github/workflows/ci.yml with the template (offline, working tree)."""
    ws = resolve_workspace(workspace)
    rows = [(pkg.name, ci_status(pkg)) for pkg in ci_targets(ws, include_skipped=include_skipped)]
    console.print(ci_status_table(rows))


@ci_app.command('sync')
def ci_sync_command(  # noqa: PLR0913, PLR0917
    packages: Annotated[
        list[str] | None,
        typer.Argument(
            help='Packages to target. Without names (and without --all): interactive menu.', show_default=False
        ),
    ] = None,
    workspace: WorkspaceOpt = None,
    all_packages: Annotated[bool, typer.Option('--all', help='Target every package except core.')] = False,
    include_skipped: IncludeSkippedOpt = False,
    dry_run: Annotated[bool, typer.Option('--dry-run', help='Only check and report; change nothing.')] = False,
    yes: Annotated[bool, typer.Option('--yes', '-y', help='Do not ask for confirmation.')] = False,
) -> None:
    """Install or update the managed CI workflow, through a PR on branch datoso-dev/ci.

    Interactive by default: pick packages from a menu. ``PKG...`` or ``--all`` run directly (after one confirmation,
    unless ``--yes``).
    """
    if packages and all_packages:
        console.print('[bold red]Error:[/] pass either package names or --all, not both.')
        raise typer.Exit(2)
    if not packages and not all_packages and not dry_run:
        _run_ci_menu(workspace, include_skipped=include_skipped)
    ws = resolve_workspace(workspace)
    try:
        targets = ci_targets(ws, include_skipped=include_skipped, only=packages or None)
    except WorkspaceError as exc:
        console.print(f'[bold red]Error:[/] {exc}')
        raise typer.Exit(2) from exc
    git, gh = make_git(), make_gh()
    with console.status('Checking repositories…', spinner='dots'):
        plan = ci_sync_all(targets, git, gh, dry_run=True)
    todo = [r.package for r in plan if r.outcome in {CiOutcome.OPENED, CiOutcome.UPDATED}]
    errors = any(r.outcome is CiOutcome.ERROR for r in plan)
    title = 'ci sync (dry run — nothing changed)' if dry_run else ('ci sync — plan' if todo else 'ci sync')
    console.print(ci_sync_table(plan, title=title))
    if dry_run or not todo:
        if not todo:
            console.print('[green]Nothing to do.[/]')
        raise typer.Exit(1 if errors else 0)
    if not yes:
        typer.confirm(f'Open or update {len(todo)} PR(s)?', abort=True)
    with console.status('Opening pull requests…', spinner='dots'):
        results = ci_sync_all(todo, git, gh)
    console.print(ci_sync_table(results, title='ci sync'))
    print_pr_urls(console, results)
    if errors or any(r.outcome is CiOutcome.ERROR for r in results):
        raise typer.Exit(1)


# --------------------------------------------------------------------------- release / bump / deps


def _fail(message: str, code: int = 2) -> NoReturn:
    console.print(f'[bold red]Error:[/] {message}')
    raise typer.Exit(code)


def _collect(ws: Workspace, *, offline: bool = False) -> dict[str, PackageState]:
    with console.status('Collecting repository state…', spinner='dots'):
        states = collect_states(ws, make_git(), make_pypi(), fetch=not offline, query_pypi=not offline)
    return {state.package.name: state for state in states}


@app.command()
def release(  # noqa: C901, PLR0912, PLR0913, PLR0917
    workspace: WorkspaceOpt = None,
    only: Annotated[
        str | None, typer.Option(help='Comma-separated packages (short names ok). Interactive: pre-selects them.')
    ] = None,
    bump_part: Annotated[
        Part | None, typer.Option('--bump', help='Version bump for every selected package (skips the questions).')
    ] = None,
    pre: Annotated[str | None, typer.Option(help='Pre-release label: a, b or rc.')] = None,
    include_skipped: IncludeSkippedOpt = False,
    dry_run: Annotated[bool, typer.Option('--dry-run', help='Show the plan and stop.')] = False,
    yes: Annotated[bool, typer.Option('--yes', '-y', help='Do not ask; requires --only and --bump.')] = False,
) -> None:
    """Release packages: pick packages and versions, review the plan, open the release PRs (step 1).

    Interactive by default. Later steps (packages depending on others in the batch) wait until step 1 is merged and
    published; run `release` again then.
    """
    ws = resolve_workspace(workspace)
    try:
        only_names = [resolve_name(ws, n.strip()) for n in only.split(',') if n.strip()] if only else None
    except WorkspaceError as exc:
        _fail(str(exc))
    interactive = is_interactive() and not yes
    if not interactive and not (only_names and bump_part and (yes or dry_run)):
        _fail('not a terminal: pass --only, --bump and --yes (or --dry-run).')

    states = _collect(ws)
    print_status(console, list(states.values()))
    prompter = make_prompter() if interactive else None
    try:
        if prompter:
            selection = choose_release(
                ws, states, prompter, console, only=only_names, part=bump_part, pre=pre, include_skipped=include_skipped
            )
            if selection is None:
                raise typer.Exit(0)
        else:
            selection = {name: target_version(states[name], bump_part, pre) for name in only_names}
        plan = plan_release(ws, states, selection, include_skipped=include_skipped)
    except (PlanError, VersionError) as exc:
        _fail(str(exc))

    console.print(plan_table(plan))
    if dry_run:
        raise typer.Exit(0 if plan.ok else 1)
    runnable = plan.runnable_now
    if not runnable:
        _fail('nothing can be released now: every step-1 package is blocked.', 1)
    if plan.waiting:
        names = ', '.join(item.name for item in plan.waiting)
        console.print(f'[yellow]{names} waits for step 1 to be merged and published; run `release` again then.[/]')
    question = f'Open {len(runnable)} release PR(s)?'
    if prompter and not prompter.confirm(question, default=True):
        raise typer.Exit(0)

    def progress(name: str, message: str) -> None:
        console.print(f'  {name}: {message}', highlight=False)

    results = open_release_prs(plan, ws, states, make_git(), make_gh(), on_progress=progress)
    console.print(release_results_table(results))
    for result in results:
        if result.pr_url:
            console.print(f'{result.name}: {result.pr_url}', soft_wrap=True, highlight=False)
    if not plan.ok or any(r.outcome is ReleaseOutcome.ERROR for r in results):
        raise typer.Exit(1)


@app.command('bump')
def bump_command(
    package: Annotated[str, typer.Argument(help='Package (short names ok).')],
    part: Annotated[Part, typer.Argument(help='patch, minor or major.')],
    workspace: WorkspaceOpt = None,
    pre: Annotated[str | None, typer.Option(help='Pre-release label: a, b or rc.')] = None,
) -> None:
    """Bump a package's version in the working tree only (no git, no dependency floors)."""
    ws = resolve_workspace(workspace)
    try:
        pkg = ws.packages[resolve_name(ws, package)]
        new = pkg.version if part is Part.KEEP else bump(pkg.version, part, pre=pre)
    except (WorkspaceError, VersionError) as exc:
        _fail(str(exc))
    target = pkg.version_file or pkg.path / 'pyproject.toml'
    setter = set_init_version if pkg.version_file else set_project_version
    target.write_text(setter(target.read_text(encoding='utf-8'), new), encoding='utf-8')
    console.print(f'{pkg.name}: {pkg.version} → {new}', highlight=False)


@app.command()
def deps(
    workspace: WorkspaceOpt = None,
    fix: Annotated[bool, typer.Option('--fix', help='Raise the floors in the working tree (no git).')] = False,
    offline: Annotated[bool, typer.Option('--offline', help='Use git tags only (no fetch, no PyPI).')] = False,
    include_skipped: IncludeSkippedOpt = False,
) -> None:
    """Show dependency floors that lag behind the latest released core / IA plugin."""
    ws = resolve_workspace(workspace)
    states = _collect(ws, offline=offline)
    released = {name: released_version(state) for name, state in states.items()}
    found = violations(ws, released, include_skipped=include_skipped)
    if not found:
        console.print('[green]Every floor follows the latest release.[/]')
        return
    console.print(deps_table(found))
    if not fix:
        console.print('Run `datoso-dev deps --fix` to raise them in the working tree.')
        raise typer.Exit(1)
    for name, changes in found.items():
        path = ws.packages[name].path / 'pyproject.toml'
        text = path.read_text(encoding='utf-8')
        for change in changes:
            text, _ = set_floor(text, change.dist, change.target)
        path.write_text(text, encoding='utf-8')
    console.print(f'[green]Raised floors in {len(found)} package(s).[/] Review with git diff and release them.')
