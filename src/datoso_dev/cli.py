"""`datoso-dev` command line interface."""

from __future__ import annotations

import json
import os
from contextlib import nullcontext
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from datoso_dev import __version__
from datoso_dev.io.git import Git
from datoso_dev.io.pypi import PyPI
from datoso_dev.ports import GitPort, PyPIPort
from datoso_dev.state import DEFAULT_BRANCH, PackageState, Status, collect_states
from datoso_dev.sync import Outcome, sync_all
from datoso_dev.ui import print_status, sync_table
from datoso_dev.workspace import Workspace, WorkspaceError, discover, find_workspace_root

app = typer.Typer(help='Release and maintenance tool for the datoso workspace.', no_args_is_help=True)
console = Console()

WORKSPACE_ENV = 'DATOSO_DEV_WORKSPACE'


# Factories are module-level so tests can replace them with fakes.
def make_git() -> GitPort:
    """Real git adapter."""
    return Git()


def make_pypi() -> PyPIPort:
    """Real PyPI adapter."""
    return PyPI()


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
