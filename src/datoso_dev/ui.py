"""Rich rendering for datoso-dev commands."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table
from rich.text import Text

from datoso_dev.ci import CiOutcome, CiResult, CiStatus
from datoso_dev.planner import Plan
from datoso_dev.policy import FloorChange
from datoso_dev.release import Outcome as ReleaseOutcome
from datoso_dev.release import ReleaseResult
from datoso_dev.state import PackageState, Status
from datoso_dev.sync import Outcome, SyncResult

STATUS_STYLE = {
    Status.NEEDS_RELEASE: 'bold yellow',
    Status.UP_TO_DATE: 'green',
    Status.SKIPPED: 'dim',
    Status.ERROR: 'bold red',
}
OUTCOME_STYLE = {
    Outcome.UPDATED: 'cyan',
    Outcome.UP_TO_DATE: 'green',
    Outcome.SKIPPED: 'yellow',
    Outcome.ERROR: 'bold red',
}

CI_STATUS_STYLE = {CiStatus.UP_TO_DATE: 'green', CiStatus.MISSING: 'yellow', CiStatus.OUTDATED: 'cyan'}
CI_OUTCOME_STYLE = {
    CiOutcome.UP_TO_DATE: 'green',
    CiOutcome.OPENED: 'cyan',
    CiOutcome.UPDATED: 'cyan',
    CiOutcome.PR_OPEN: 'green',
    CiOutcome.SKIPPED: 'yellow',
    CiOutcome.ERROR: 'bold red',
}

_planned = {CiOutcome.OPENED: 'will open PR', CiOutcome.UPDATED: 'will update PR'}

EMPTY = '\N{EN DASH}'


def _dash(value: object) -> str:
    return EMPTY if value is None else str(value)


def status_table(states: list[PackageState]) -> Table:
    """Dashboard table, one row per package."""
    table = Table(title='datoso workspace', title_justify='left', header_style='bold')
    for column in ('Package', 'Version', 'Last tag', 'PyPI', 'Unreleased', 'Branch', 'Status', 'Issues'):
        table.add_column(column, no_wrap=column in {'Package', 'Status'})
    for state in states:
        status = state.status
        unreleased = '' if state.error else (str(state.commits_since_tag) if state.commits_since_tag else EMPTY)
        issues = state.error or '; '.join(state.issues)
        table.add_row(
            state.package.name,
            str(state.package.version),
            _dash(state.last_tag),
            _dash(state.pypi_version),
            unreleased,
            _dash(state.branch),
            Text(str(status), style=STATUS_STYLE[status]),
            Text(issues, style='red' if state.error else 'yellow'),
        )
    return table


def print_status(console: Console, states: list[PackageState]) -> None:
    """Print the dashboard plus a one-line summary."""
    console.print(status_table(states))
    pending = [s.package.name for s in states if s.status is Status.NEEDS_RELEASE]
    errors = [s.package.name for s in states if s.status is Status.ERROR]
    if pending:
        console.print(f'[bold yellow]{len(pending)} package(s) need a release:[/] {", ".join(pending)}')
    else:
        console.print('[green]Nothing to release.[/]')
    if errors:
        console.print(f'[bold red]{len(errors)} package(s) could not be read:[/] {", ".join(errors)}')


def sync_table(results: list[SyncResult], *, dry_run: bool) -> Table:
    """Table of sync outcomes."""
    title = 'sync (dry run — nothing changed)' if dry_run else 'sync'
    table = Table(title=title, title_justify='left', header_style='bold')
    for column in ('Repository', 'Result', 'Branch', 'Detail'):
        table.add_column(column)
    for result in results:
        branch = f'{result.switched_from} → master' if result.switched_from else ''
        table.add_row(
            result.path.name, Text(str(result.outcome), style=OUTCOME_STYLE[result.outcome]), branch, result.detail
        )
    return table


def ci_status_table(rows: list[tuple[str, CiStatus]]) -> Table:
    """Managed-workflow status per package."""
    table = Table(title='ci workflow', title_justify='left', header_style='bold')
    table.add_column('Package', no_wrap=True)
    table.add_column('Workflow')
    for name, status in rows:
        table.add_row(name, Text(str(status), style=CI_STATUS_STYLE[status]))
    return table


def ci_sync_table(results: list[CiResult], *, title: str) -> Table:
    """Table of ``ci sync`` outcomes (planned or done)."""
    table = Table(title=title, title_justify='left', header_style='bold')
    table.add_column('Package', no_wrap=True)
    table.add_column('Result', no_wrap=True)
    table.add_column('Detail')
    table.add_column('PR', overflow='fold', min_width=20)  # full URLs are printed below by print_pr_urls
    for result in results:
        table.add_row(
            result.package.name,
            Text(planned_label(result), style=CI_OUTCOME_STYLE[result.outcome]),
            result.detail,
            result.pr_url or '',
        )
    return table


def planned_label(result: CiResult) -> str:
    """Outcome wording: future tense for a dry run ('will open PR'), past tense otherwise."""
    return _planned.get(result.outcome, str(result.outcome)) if result.dry_run else str(result.outcome)


def print_pr_urls(console: Console, results: list[CiResult]) -> None:
    """Print each PR URL on its own unwrapped line (tables would truncate or fold them)."""
    for result in results:
        if result.pr_url:
            console.print(f'{result.package.name}: {result.pr_url}', soft_wrap=True, highlight=False)


def floor_text(change: FloorChange) -> str:
    """``datoso>=1.1.0 → datoso>=1.1.1`` (with ``[extra]`` for core extras)."""
    before = f'{change.dist}>={change.current}' if change.current else change.dist
    extra = f'[{change.extra}] ' if change.extra else ''
    return f'{extra}{before} → {change.dist}>={change.target}'


def plan_table(plan: Plan) -> Table:
    """Render the release plan: one row per package, in step order."""
    table = Table(title='Release plan', title_justify='left', header_style='bold')
    for column in ('Step', 'Package', 'Version', 'Dependency floors', 'Status'):
        table.add_column(column, no_wrap=column in {'Step', 'Package', 'Version'})
    first = {item.name for item in plan.steps[0]}
    for item in plan.items:
        if item.blockers:
            status = Text('blocked: ' + '; '.join(item.blockers), style='bold red')
        elif item.name in first:
            status = Text('ready', style='green')
        else:
            status = Text('waits for step 1 to be merged and published', style='yellow')
        table.add_row(
            str(item.step),
            item.name,
            f'{item.released or "unreleased"} → {item.target}',
            Text('\n'.join(floor_text(c) for c in item.floor_changes) or EMPTY),  # Text: no markup ([extra])
            status,
        )
    if plan.extras:
        table.add_row(
            'then',
            'datoso (core extras)',
            EMPTY,
            Text('\n'.join(floor_text(c) for c in plan.extras)),
            Text('direct commit to core master after the releases (phase 4)', style='dim'),
        )
    return table


RELEASE_STYLE = {
    ReleaseOutcome.OPENED: 'green',
    ReleaseOutcome.ALREADY_OPEN: 'green',
    ReleaseOutcome.NOTHING_TO_COMMIT: 'yellow',
    ReleaseOutcome.ERROR: 'bold red',
}


def release_results_table(results: list[ReleaseResult]) -> Table:
    """Outcome per package after opening release PRs."""
    table = Table(title='release', title_justify='left', header_style='bold')
    for column in ('Package', 'Result', 'Detail'):
        table.add_column(column, no_wrap=column != 'Detail')
    for result in results:
        table.add_row(result.name, Text(str(result.outcome), style=RELEASE_STYLE[result.outcome]), Text(result.detail))
    return table


def deps_table(found: dict[str, list[FloorChange]]) -> Table:
    """Dependency floors that lag behind the latest released core / IA plugin."""
    table = Table(title='Dependency floors behind the latest release', title_justify='left', header_style='bold')
    table.add_column('Package', no_wrap=True)
    table.add_column('Floor')
    for name, changes in found.items():
        table.add_row(name, Text('\n'.join(floor_text(c) for c in changes)))
    return table
