"""`datoso-dev` command line interface."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from packaging.version import Version
from rich.console import Console

from datoso_dev import __version__
from datoso_dev.ci import CiOutcome, ci_status, ci_sync_all, ci_targets
from datoso_dev.ci_menu import ci_menu
from datoso_dev.io.gh import Gh
from datoso_dev.io.git import Git
from datoso_dev.io.pypi import PyPI
from datoso_dev.pipeline import DONE, Waits, commit_pointers, release_batch, update_core_extras
from datoso_dev.planner import Plan, PlanError, plan_release, released_version
from datoso_dev.policy import violations
from datoso_dev.ports import GhError, GhPort, GitPort, PyPIPort
from datoso_dev.pr import (
    PrAction,
    PrItem,
    PrText,
    default_title,
    editor_template,
    name_branch,
    parse_template,
    plan_prs,
    run_prs,
    suggested_branch,
)
from datoso_dev.prompts import Option, Prompter, QuestionaryPrompter
from datoso_dev.prs import OpenPr, list_open, merge
from datoso_dev.pyproject import set_floor, set_init_version, set_project_version
from datoso_dev.release import Action, Probe, ReleaseResult, probe_releases, run_releases
from datoso_dev.release import Outcome as ReleaseOutcome
from datoso_dev.release_menu import choose_release, target_version
from datoso_dev.state import DEFAULT_BRANCH, PackageState, Status, collect_states, latest_release_tag
from datoso_dev.sync import Outcome, sync_all
from datoso_dev.ui import (
    ci_status_table,
    ci_sync_table,
    deps_table,
    plan_table,
    pr_table,
    print_pr_urls,
    print_status,
    prs_table,
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


def make_waits() -> Waits:
    """CI / PyPI timeouts and clock for the full release (tests patch it)."""
    return Waits()


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


# --------------------------------------------------------------------------- pr


def _pr_text(todo: list[PrItem], title: str | None, body: str, *, yes: bool) -> PrText | None:
    """Title and description for new PRs: from --title, else from the editor (``None``: no new PR)."""
    if title:
        return PrText(title, body)
    if not any(item.action is PrAction.OPEN for item in todo):
        return None
    if yes or not is_interactive():
        _fail('--title is needed to open new PRs without a terminal.')
    text = _edit_pr_text(editor_template(todo, default_title(todo)))
    if text is None:
        _fail('Cancelled: empty title.', 1)
    return text


def _branch_name(items: list[PrItem], branch: str | None, *, yes: bool) -> str | None:
    """Branch for commits made on master: --branch, else asked (suggesting the run's feature branch)."""
    if branch or not any(item.from_base for item in items) or yes or not is_interactive():
        return branch
    name = typer.prompt('New branch for the commits on master', default=suggested_branch(items) or '')
    return name.strip() or None


def _edit_pr_text(template: str) -> PrText | None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'PULL_REQUEST.md'
        path.write_text(template, encoding='utf-8')
        try:
            run_editor(path)
        except (OSError, subprocess.CalledProcessError) as exc:
            _fail(f'editor failed: {exc}', 1)
        return parse_template(path.read_text(encoding='utf-8'))


@app.command('pr')
def pr_command(  # noqa: PLR0913, PLR0917
    packages: Annotated[
        list[str] | None,
        typer.Argument(help='Only these packages (short names ok). Default: every repo on a feature branch.'),
    ] = None,
    workspace: WorkspaceOpt = None,
    title: Annotated[str | None, typer.Option(help='Title of new PRs (skips the editor).')] = None,
    body: Annotated[str, typer.Option(help='Description of new PRs, with --title.')] = '',
    branch: Annotated[
        str | None, typer.Option(help='Branch to move commits made on master to (default: asked).')
    ] = None,
    dry_run: Annotated[bool, typer.Option('--dry-run', help='Show the plan and stop.')] = False,
    yes: Annotated[bool, typer.Option('--yes', '-y', help='Do not ask for confirmation.')] = False,
) -> None:
    """Push committed feature branches and open or update one PR per repo, linked to each other.

    Never commits: repos with uncommitted changes are skipped. New PRs are drafts while a datoso>= floor they need is
    not on PyPI; a later run marks them ready. The title and description (shared, plus a note per repo) are written in
    your editor unless --title is given.
    """
    ws = resolve_workspace(workspace)
    try:
        targets = [ws.packages[resolve_name(ws, name)] for name in packages] if packages else ws.packages.values()
    except WorkspaceError as exc:
        _fail(str(exc))
    git, gh, pypi = make_git(), make_gh(), make_pypi()
    with console.status('Checking repositories…', spinner='dots'):
        items = plan_prs(ws, targets, git, gh, pypi)
    if not items:
        console.print('[green]No feature branches with commits to propose.[/]')
        return
    errors = any(item.action is PrAction.ERROR for item in items)
    console.print(pr_table(items, title='pr (dry run — nothing changed)' if dry_run else 'pr — plan'))
    if not dry_run and any(item.from_base for item in items):
        items = name_branch(items, _branch_name(items, branch, yes=yes))
    todo = [item for item in items if item.actionable]
    if dry_run or not todo:
        if not todo:
            console.print('[green]Nothing to push or open.[/]')
        raise typer.Exit(1 if errors else 0)
    text = _pr_text(todo, title, body, yes=yes)
    if not yes:
        typer.confirm(f'Push and open or update {len(todo)} PR(s)?', abort=True)
    with console.status('Pushing and opening pull requests…', spinner='dots'):
        results, link_errors = run_prs(items, git, gh, text)
    console.print(pr_table(results, title='pr'))
    _print_links(item.pr_url for item in results)
    for error in link_errors:
        console.print(f'[yellow]Could not link:[/] {error}', highlight=False)
    if errors or any(item.action is PrAction.ERROR for item in results):
        raise typer.Exit(1)


# --------------------------------------------------------------------------- prs


def _repo_paths(ws: Workspace, names: list[str] | None) -> list[Path]:
    """Every repository of the workspace, or the named ones (short names ok, e.g. ``redump``)."""
    if not names:
        return list(ws.submodule_paths)
    by_name = {path.name: path for path in ws.submodule_paths}
    paths = []
    for name in names:
        match = next((by_name[c] for c in (name, f'datoso_seed_{name}', f'datoso_plugin_{name}') if c in by_name), None)
        if match is None:
            _fail(f'unknown repository: {name}')
        paths.append(match)
    return paths


def _pick_prs(prs: list[OpenPr], *, include_failing: bool, yes: bool) -> list[OpenPr]:
    """Return the PRs to merge: picked in a checkbox (green ones ticked), or with --yes the green ones."""
    candidates = [item for item in prs if item.mergeable]
    if yes:
        return candidates if include_failing else [item for item in candidates if item.green]
    if not is_interactive():
        _fail('not a terminal: pass --yes (and --include-failing to merge PRs with failing CI).')
    options = [
        Option(f'{item.repo.name} #{item.pr.number} {item.pr.title} [CI {item.pr.checks}]', item,
               disabled=item.blocker or None, checked=item.green)
        for item in prs
    ]  # fmt: skip
    return make_prompter().checkbox('Merge which PRs?', options) or []


@app.command('prs')
def prs_command(
    repos: Annotated[
        list[str] | None, typer.Argument(help='Only these repositories (short names ok). Default: all.')
    ] = None,
    workspace: WorkspaceOpt = None,
    do_merge: Annotated[bool, typer.Option('--merge', help='Pick PRs to squash-merge (only yours).')] = False,
    include_failing: Annotated[
        bool,
        typer.Option('--include-failing', help='With --merge --yes: also merge PRs with failing CI.'),
    ] = False,
    yes: Annotated[bool, typer.Option('--yes', '-y', help='Do not ask (merges every green PR of yours).')] = False,
) -> None:
    """List the open PRs of every repository; with --merge, squash-merge the ones you pick.

    Only PRs opened by the account `gh` is logged in as can be merged (re-checked right before each merge), and not
    drafts or PRs with conflicts. Merged repos are then switched to master and pulled, ready for `release`.
    """
    ws = resolve_workspace(workspace)
    paths = _repo_paths(ws, repos)
    gh = make_gh()
    with console.status('Listing pull requests…', spinner='dots'):
        try:
            me = gh.current_user(ws.root)
        except GhError as exc:
            _fail(str(exc), 1)
        prs, errors = list_open(paths, gh, me)
    for error in errors:
        console.print(f'[bold red]Could not list:[/] {error}', highlight=False)
    if not prs:
        console.print('[green]No open pull requests.[/]')
        raise typer.Exit(1 if errors else 0)
    console.print(prs_table(prs, title=f'open pull requests (you: {me})'))
    if not do_merge:
        raise typer.Exit(1 if errors else 0)
    chosen = _pick_prs(prs, include_failing=include_failing, yes=yes)
    if not chosen:
        console.print('Nothing to merge.')
        raise typer.Exit(1 if errors else 0)
    if not yes:
        typer.confirm(f'Squash-merge {len(chosen)} PR(s) and delete their branches?', abort=True)
    with console.status('Merging…', spinner='dots'):
        results = merge(chosen, gh, me)
    console.print(prs_table(results, title='merge'))
    merged = list(dict.fromkeys(item.repo for item in results if item.merged))
    if merged:
        with console.status('Switching merged repos to master…', spinner='dots'):
            synced = sync_all(merged, make_git())
        console.print(sync_table(synced, dry_run=False))
        errors += [str(result.path) for result in synced if result.outcome is Outcome.ERROR]
    if errors or any(item.error or item.blocker for item in results):
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
def release(  # noqa: PLR0913, PLR0917
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
    no_wait: Annotated[bool, typer.Option('--no-wait', help='Do not wait for PyPI after publishing.')] = False,
    merge: Annotated[
        bool, typer.Option('--merge', help='With --yes: full release (wait for CI, merge, publish, every step).')
    ] = False,
) -> None:
    """Release packages: pick packages and versions, review the plan, then release them.

    Full release (interactive choice, or --yes --merge): per step, PR → CI → merge → GitHub Release (triggers the PyPI
    upload) → PyPI listed; then core's extras floors and the workspace pointers. Or stop at the PRs (--yes): you merge
    on GitHub and run `release` again. Every run resumes from what GitHub and PyPI already show.
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
    previous: dict[str, Version] | None = None
    while True:  # Esc on "What should this run do?" goes back to the versions
        selection = _select(ws, states, prompter, only_names, bump_part, pre, include_skipped, previous)
        try:
            plan = plan_release(ws, states, selection, include_skipped=include_skipped)
        except PlanError as exc:
            _fail(str(exc))
        probes = probe_releases(plan, ws, make_git(), make_gh())
        console.print(plan_table(plan, probes))
        if dry_run:
            raise typer.Exit(0 if plan.ok else 1)
        if not plan.runnable_now:
            _fail('nothing can be released now: every step-1 package is blocked.', 1)
        mode = _choose_mode(probes, prompter, merge=merge)
        if mode is not None:
            break
        previous = selection
    if mode == FULL:
        _full_release(plan, probes, ws, states, wait=not no_wait)
    else:
        _step_release(plan, probes, ws, states, wait=not no_wait)


def _select(  # noqa: PLR0913, PLR0917
    ws: Workspace,
    states: dict[str, PackageState],
    prompter: Prompter | None,
    only: list[str] | None,
    part: Part | None,
    pre: str | None,
    include_skipped: bool,
    previous: dict[str, Version] | None,
) -> dict[str, Version]:
    """Package → target version, from the wizard (Esc on the packages exits) or from --only/--bump."""
    try:
        if prompter is None:
            return {name: target_version(states[name], part, pre) for name in only or []}  # type: ignore[arg-type]
        selection = choose_release(
            ws, states, prompter, console, only=only, part=part, pre=pre, include_skipped=include_skipped,
            previous=previous,
        )  # fmt: skip
    except VersionError as exc:
        _fail(str(exc))
    if selection is None:
        raise typer.Exit(0)
    return selection


FULL, STEP = 'full', 'step'
FULL_LABEL = 'Full release: PR → CI → merge → GitHub Release → PyPI, every step; then core extras floors and pointers'


def _step_question(probes: dict[str, Probe]) -> str | None:
    counts = Counter(probe.action for probe in probes.values())
    parts = []
    if counts[Action.OPEN_PR]:
        parts.append(f'open {counts[Action.OPEN_PR]} release PR(s)')
    if counts[Action.PUBLISH]:
        parts.append(f'publish {counts[Action.PUBLISH]} release(s)')
    text = ' and '.join(parts)
    return f'{text[0].upper()}{text[1:]}' if parts else None


def _choose_mode(probes: dict[str, Probe], prompter: Prompter | None, *, merge: bool) -> str | None:
    """Full release, or one step that stops at the PRs (interactive: ask; else ``--merge`` decides)."""
    if prompter is None:
        return FULL if merge else STEP
    step = _step_question(probes)
    options = [
        Option(FULL_LABEL, FULL),
        Option(
            f'{step}, then stop (you merge on GitHub)' if step else 'Stop at the PRs',
            STEP,
            disabled=None if step else 'nothing to open or publish: the PRs wait for a merge',
        ),
    ]
    return prompter.select('What should this run do?', options)


def _progress(name: str, message: str) -> None:
    console.print(f'  {name}: {message}', highlight=False)


def _print_results(results: list[ReleaseResult]) -> None:
    console.print(release_results_table(results))
    for result in results:
        if result.pr_url:
            console.print(f'{result.name}: {result.pr_url}', soft_wrap=True, highlight=False)


def _full_release(
    plan: Plan, probes: dict[str, Probe], ws: Workspace, states: dict[str, PackageState], *, wait: bool
) -> None:
    git, gh, pypi = make_git(), make_gh(), make_pypi()
    if plan.waiting:
        names = ', '.join(item.name for item in plan.waiting)
        console.print(f'[dim]{names}: starts once its dependencies in this batch are on PyPI.[/]')
    waits = replace(make_waits(), wait_pypi=wait)
    batch = release_batch(plan, ws, states, git, gh, pypi, probes=probes, waits=waits, on_progress=_progress)
    results = list(batch.results)
    extras = update_core_extras(ws, git, batch.published)
    raised = extras is not None and extras.outcome is ReleaseOutcome.UPDATED
    pointers = commit_pointers(ws, git, batch.published, extras=raised)
    results += [step for step in (extras, pointers) if step]
    _print_results(results)
    unfinished = [r for r in batch.results if r.outcome not in DONE]
    if unfinished:
        console.print('Not finished: fix what the table shows, then run `datoso-dev release` again to continue.')
    if not plan.ok or unfinished or any(r.outcome is ReleaseOutcome.ERROR for r in results):
        raise typer.Exit(1)
    console.print('[green]Released.[/]')


def _step_release(
    plan: Plan, probes: dict[str, Probe], ws: Workspace, states: dict[str, PackageState], *, wait: bool
) -> None:
    if _step_question(probes) is None:
        console.print('[yellow]Nothing to do now: merge the open release PR(s), then run `release` again.[/]')
        _print_links(probe.pr_url for probe in probes.values())
        raise typer.Exit(0 if plan.ok else 1)
    if plan.waiting:
        names = ', '.join(item.name for item in plan.waiting)
        console.print(f'[yellow]{names} waits for step 1 to be published; run `release` again then.[/]')
    results = run_releases(
        plan, probes, ws, states, make_git(), make_gh(), make_pypi(), wait=wait, on_progress=_progress
    )
    _print_results(results)
    outcomes = {result.outcome for result in results}
    if outcomes & {ReleaseOutcome.OPENED, ReleaseOutcome.ALREADY_OPEN}:
        console.print('Merge the PR(s), then run `datoso-dev release` again to tag and publish.')
    if outcomes & {ReleaseOutcome.PUBLISHED, ReleaseOutcome.PYPI_PENDING}:
        console.print('Released. Next: `datoso-dev sync`, then commit the submodule pointers in the workspace.')
    if not plan.ok or ReleaseOutcome.ERROR in outcomes:
        raise typer.Exit(1)


def _print_links(urls: Iterable[str | None]) -> None:
    for url in urls:
        if url:
            console.print(url, soft_wrap=True, highlight=False)


def run_editor(path: Path) -> None:
    """Open ``path`` in ``$VISUAL`` / ``$EDITOR`` (default ``vi``) and wait for it to close."""
    editor = os.environ.get('VISUAL') or os.environ.get('EDITOR') or 'vi'
    subprocess.run([*shlex.split(editor), str(path)], check=True)  # noqa: S603 - the user's own editor


@app.command()
def notes(
    package: Annotated[str, typer.Argument(help='Package (short names ok).')],
    version: Annotated[str | None, typer.Argument(help='Release version (default: the latest tag).')] = None,
    workspace: WorkspaceOpt = None,
) -> None:
    """Edit a GitHub Release's notes in your editor (does not re-trigger the PyPI upload)."""
    ws = resolve_workspace(workspace)
    try:
        pkg = ws.packages[resolve_name(ws, package)]
    except WorkspaceError as exc:
        _fail(str(exc))
    git, gh = make_git(), make_gh()
    if version:
        tag = f'v{version.removeprefix("v")}'
    else:
        latest = latest_release_tag(git.tags(pkg.path))
        if latest is None:
            _fail(f'{pkg.name} has no release tag yet.', 1)
        tag = latest[0]
    try:
        if gh.release_url(pkg.path, tag) is None:
            _fail(f'{pkg.name}: no GitHub Release {tag}.', 1)
        body = gh.release_notes(pkg.path, tag).rstrip('\n') + '\n'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f'{pkg.name}-{tag}.md'
            path.write_text(body, encoding='utf-8')
            run_editor(path)
            if path.read_text(encoding='utf-8') == body:
                console.print(f'{pkg.name} {tag} notes unchanged.', highlight=False)
                return
            gh.release_edit_notes(pkg.path, tag, path)
    except (GhError, OSError, subprocess.CalledProcessError) as exc:
        _fail(str(exc), 1)
    console.print(f'{pkg.name} {tag} notes updated.', highlight=False)


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
