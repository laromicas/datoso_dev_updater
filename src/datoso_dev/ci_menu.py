"""Interactive `datoso-dev ci` menu: show the workflow status, then let the user pick what to sync."""

from __future__ import annotations

import difflib
from enum import StrEnum

from rich.console import Console
from rich.syntax import Syntax

from datoso_dev.ci import WORKFLOW_PATH, CiOutcome, CiResult, ci_sync_all, ci_targets, render_ci
from datoso_dev.ports import GhPort, GitPort
from datoso_dev.prompts import Option, Prompter
from datoso_dev.ui import ci_sync_table, planned_label, print_pr_urls
from datoso_dev.workspace import Package, Workspace

ACTIONABLE = frozenset({CiOutcome.OPENED, CiOutcome.UPDATED})


class Action(StrEnum):
    """Top-level menu entries."""

    SYNC = 'sync'
    DIFF = 'diff'
    REFRESH = 'refresh'
    QUIT = 'quit'


MENU = [
    Option('Open / update PRs for selected packages…', Action.SYNC),
    Option('Show workflow diff for a package…', Action.DIFF),
    Option('Refresh status', Action.REFRESH),
    Option('Quit', Action.QUIT),
]


def selection_options(plan: list[CiResult]) -> list[Option]:
    """One checkbox entry per package; only those needing a PR can be picked."""
    width = max((len(r.package.name) for r in plan), default=0)
    options = []
    for result in plan:
        label = f'{result.package.name:<{width}}  {planned_label(result)}'
        if result.outcome in ACTIONABLE:
            options.append(Option(label, result.package))
        else:
            reason = f'{result.outcome}: {result.detail}' if result.detail else str(result.outcome)
            options.append(Option(result.package.name, result.package, disabled=reason))
    return options


def ci_menu(  # noqa: PLR0913
    ws: Workspace,
    git: GitPort,
    gh: GhPort,
    prompter: Prompter,
    console: Console,
    *,
    include_skipped: bool = False,
) -> int:
    """Run the menu until the user quits. Return 1 if any sync failed, else 0."""
    targets = ci_targets(ws, include_skipped=include_skipped)
    failed = False
    plan: list[CiResult] | None = None
    while True:
        if plan is None:
            with console.status('Checking repositories…', spinner='dots'):
                plan = ci_sync_all(targets, git, gh, dry_run=True)
            console.print(ci_sync_table(plan, title='ci workflow status'))
        action = prompter.select('What do you want to do?', MENU)
        if action in {None, Action.QUIT}:
            return 1 if failed else 0
        if action is Action.REFRESH:
            plan = None
        elif action is Action.DIFF:
            _show_diff(plan, prompter, console)
        elif action is Action.SYNC:
            results = _sync_selected(plan, git, gh, prompter, console)
            if results is not None:
                failed |= any(r.outcome is CiOutcome.ERROR for r in results)
                plan = None  # re-check after pushing


def _sync_selected(
    plan: list[CiResult], git: GitPort, gh: GhPort, prompter: Prompter, console: Console
) -> list[CiResult] | None:
    """Ask which packages to sync, confirm, and run. ``None`` when nothing was done."""
    if not any(r.outcome in ACTIONABLE for r in plan):
        console.print('[green]Nothing to do:[/] every selectable workflow is up to date.')
        return None
    picked: list[Package] = prompter.checkbox('Which packages?', selection_options(plan)) or []
    if not picked:
        return None
    if not prompter.confirm(f'Open or update {len(picked)} PR(s)?'):
        return None
    with console.status('Opening pull requests…', spinner='dots'):
        results = ci_sync_all(picked, git, gh)
    console.print(ci_sync_table(results, title='ci sync'))
    print_pr_urls(console, results)
    return results


def _show_diff(plan: list[CiResult], prompter: Prompter, console: Console) -> None:
    options = [Option(r.package.name, r.package) for r in plan if r.outcome in ACTIONABLE]
    if not options:
        console.print('[green]No differences:[/] every selectable workflow is up to date.')
        return
    pkg: Package | None = prompter.select('Diff for which package?', [*options, Option('Back', None)])
    if pkg is None:
        return
    workflow = pkg.path / WORKFLOW_PATH
    current = workflow.read_text(encoding='utf-8').splitlines(keepends=True) if workflow.is_file() else []
    diff = ''.join(
        difflib.unified_diff(
            current, render_ci(pkg).splitlines(keepends=True), f'a/{WORKFLOW_PATH}', f'b/{WORKFLOW_PATH}'
        )
    )
    console.print(Syntax(diff, 'diff', theme='ansi_dark', background_color='default'))
