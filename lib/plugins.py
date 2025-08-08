"""Plugin version checker."""
from calendar import c
from hmac import new
from pathlib import Path

from packaging.version import Version
from rich.console import Console

from lib.config import PATH

console = Console()

plugin_list = [
    'datoso',
    'datoso_plugin_internetarchive',
    'datoso_seed_base',
    'datoso_seed_enhanced',
    'datoso_seed_fbneo',
    'datoso_seed_nointro',
    'datoso_seed_pleasuredome',
    'datoso_seed_private',
    'datoso_seed_redump',
    'datoso_seed_tdc',
    'datoso_seed_translatedenglish',
    'datoso_seed_vpinmame',
    'datoso_seed_whdload',
]

def get_datoso_version() -> Version | None:
    """Get the version of datoso."""
    return get_plugin_version('datoso')


def get_plugin_version(plugin: str) -> Version | None:
    """Get the version of a plugin."""
    plugin_path = PATH / plugin / 'src' / plugin / '__init__.py'
    with open(plugin_path) as f:
        for line in f:
            if line.strip().startswith('__version__'):
                return Version(line.split('=')[1].strip().replace('"', '').replace("'", ''))
    return None

def get_plugin_versions() -> dict:
    """Get the versions of all plugins."""
    plugins = {}
    for plugin in plugin_list:
        plugins[plugin] = {
            'name': plugin,
            'version': get_plugin_version(plugin),
        }
    return plugins

def get_datoso_version() -> Version | None:
    """Get the version of datoso."""
    return get_plugin_version('datoso')

def update_version(plugin: str, version: str, *, dry_run: bool=False) -> None:
    """Update the version of a plugin."""
    plugin_path = PATH / plugin / 'src' / plugin
    file_data = []
    file_path = Path(plugin_path) / '__init__.py'
    with open(file_path) as f:
        for line in f:
            newline = line
            if line.startswith('__version__'):
                newline = f"__version__ = '{version}'\n"
                console.print(f'[green]Updating {plugin} version from [cyan]{line.strip()}[/cyan] to [magenta]{newline.strip()}[/magenta][/green]')
            if not line.endswith('\n'):
                newline += '\n'
            file_data.append(newline)
    if dry_run:
        console.print(f'[yellow]Dry run:[/yellow] Will update [cyan]{plugin}[/cyan] version to [magenta]{version}[/magenta]')
    else:
        with open(file_path, 'w') as f:
            f.writelines(file_data)


def update_dependencies(plugin_path: str, datoso_version: str, plugins: list[str], *, dry_run: bool=False) -> None:
    """Update the dependencies of a plugin."""
    toml_path = PATH / plugin_path / 'pyproject.toml'
    file_data = []
    # ruff: noqa: PLW2901
    with open(toml_path) as f:
        for line in f:
            if not line.endswith('\n'):
                line += '\n'
            if 'datoso>' in line.strip():
                line = f'    "datoso>={datoso_version}",\n'
            for plugin in plugins:
                plugin_name = plugin.replace('_','-')
                if line.strip().startswith(f'"{plugin_name}>'):
                    line = f'    "{plugin_name}>={get_plugin_version(plugin)}",\n'
                package = plugin_name.split('-')[-1]
                if package != 'datoso' and line.strip().startswith(f'{package} ='):
                    line = f'{package} = [ "{plugin_name}>={get_plugin_version(plugin)}" ]\n'
            file_data.append(line)
    if dry_run:
        console.print(f'[yellow]Dry run:[/yellow] Will update dependencies in [cyan]{toml_path}[/cyan]')
    else:
        with open(toml_path, 'w') as f:
            f.writelines(file_data)