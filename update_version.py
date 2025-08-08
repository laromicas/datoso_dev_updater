#!/usr/bin/env python
"""Update the version of datoso plugins and seeds."""

import typer
from lib.git import create_branch, get_modified_files, get_new_files, undo_update
from lib.plugins import get_datoso_version, get_plugin_version, plugin_list, update_dependencies, update_version
from packaging.version import Version
from rich.console import Console
from typing_extensions import Annotated

# ruff: noqa: E501, C901, FBT002

app = typer.Typer()
console = Console()

def get_new_version(plugin: str, *, patch: bool = False, minor: bool = False, major: bool = False, dev: bool = False, version: str | None = None) -> tuple[str, str]:
    """Get the new version of a plugin."""
    actual_version = get_plugin_version(plugin)
    if dev:
        version_val = get_update_dev(plugin)
    elif patch:
        version_val = get_update_patch(plugin)
    elif minor:
        version_val = get_update_minor(plugin)
    elif major:
        version_val = get_update_major(plugin)
    else:
        version_val = version
    return actual_version, version_val

def get_update_dev(plugin: str) -> Version:
    """Get the new dev version."""
    version = get_plugin_version(plugin)
    version = str(version).split('.')
    if 'dev' in version[2]:
        version[2] = version[2].split('dev')[0]
    version[2] = str(int(version[2]) + 1) + '.dev'
    return Version('.'.join(version))

def get_update_patch(plugin: str) -> Version:
    """Get the new patch version."""
    version = get_plugin_version(plugin)
    version = str(version).split('.')
    version[2] = str(int(version[2]) + 1)
    return Version('.'.join(version))

def get_update_minor(plugin: str) -> Version:
    """Get the new minor version."""
    version = get_plugin_version(plugin)
    version = str(version).split('.')
    version[1] = str(int(version[1]) + 1)
    version[2] = '0'
    return Version('.'.join(version))

def get_update_major(plugin: str) -> Version:
    """Get the new major version."""
    version = get_plugin_version(plugin)
    version = str(version).split('.')
    version[0] = str(int(version[0]) + 1)
    version[1] = '0'
    version[2] = '0'
    return Version('.'.join(version))


@app.command()
def main(
    plugin: str = typer.Option(None, help='Plugin Name'),
    version: Annotated[str, typer.Option('--version', '-v', help='New version')] = None,
    automatic: Annotated[bool, typer.Option('--automatic', '-a', help='Bump versions of all plugins with changes')] = False,
    all_: Annotated[bool, typer.Option('--all', '-A', help='Bump versions of all plugins')] = False,
    dev: Annotated[bool, typer.Option('--dev', '-d', help='Developer version (Attach .dev to version)')] = False,
    patch: Annotated[bool, typer.Option('--patch', '-p', help='Patch version (Changes x.y.z to x.y.z+1)')] = False,
    minor: Annotated[bool, typer.Option('--minor', '-m', help='Minor version (Changes x.y.z to x.y+1.0)')] = False,
    major: Annotated[bool, typer.Option('--major', '-M', help='Major version (Changes x.y.z to x+1.0.0)')] = False,
    restore: Annotated[bool, typer.Option('--restore', '-r', help='Restore version (Changes version to original)')] = False,
    dry_run: Annotated[bool, typer.Option('--dry-run', help='Dry run')] = False,
):
    """Run the main function."""

    if not any([patch, minor, major, version, restore]):
        patch = True

    if plugin and plugin not in plugin_list:
        console.print(f'[red]Plugin {plugin} not found[/red]')
        raise typer.Exit(1)

    def update_plugin(plugin: str) -> None:
        actual_version, new_version = get_new_version(plugin, patch=patch, minor=minor, major=major, version=version, dev=dev)
        update_version(plugin, new_version, dry_run=dry_run)
        console.print(f'[green]Updated version files [cyan]{plugin}[/cyan] from [blue]{actual_version}[/blue] to [magenta]{new_version}[/magenta][/green]')
        #create a branch for the update with branch name = new version
        create_branch(plugin, str(new_version), dry_run=dry_run)
        console.print(f'[green]Created branch [cyan]{plugin}[/cyan] for version [magenta]{new_version}[/magenta][/green]')

    datoso_version = get_datoso_version()

    if automatic or all_:
        for plg in plugin_list:
            undo_update(plg)
            if restore:
                continue

            newfiles = get_new_files(plg)
            modified = get_modified_files(plg)
            all_files = [x for x in newfiles + modified if x]
            if all_files or all_:
                console.print(f'Plugin [cyan]{plg}[/cyan]')
                console.print('[yellow]Files:[/yellow]')
                console.print(all_files)
                update_plugin(plg)
    elif restore:
        undo_update(plugin)
    else:
        update_plugin(plugin)

    for plg in plugin_list:
        if 'plugin' not in plg or plg == plugin:
            update_dependencies(plg, datoso_version, plugin_list, dry_run=dry_run)


if __name__ == '__main__':
    app()
