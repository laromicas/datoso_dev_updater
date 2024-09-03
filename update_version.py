#!/usr/bin/env python3
"""Update the version of datoso plugins and seeds."""
import subprocess
import sys
from argparse import ArgumentParser, Namespace
from contextlib import suppress
from pathlib import Path

from config import PATH
from packaging.version import Version
from pip._vendor.pygments.console import colorize
from plugins import get_plugin_version, plugin_list

# ruff: noqa: E501, C901

def parse_args() -> Namespace:
    """Parse arguments."""
    parser = ArgumentParser(description='Update the version of a plugin and seed')

    plugin_parser = parser.add_mutually_exclusive_group(required=True)
    plugin_parser.add_argument('--plugin', help='Plugin Name')
    plugin_parser.add_argument('-a', '--automatic', help='Bump versions of all plugins', action='store_true')
    plugin_parser.add_argument('-A', '--all', help='Bump versions of all plugins', action='store_true')


    version_parser = parser.add_mutually_exclusive_group(required=True)
    version_parser.add_argument('-v', '--version', help='New version')
    version_parser.add_argument('-d', '--dev', help='Developer version', action='store_true')
    version_parser.add_argument('-p', '--patch', help='Patch version', action='store_true')
    version_parser.add_argument('-m', '--minor', help='Minor version', action='store_true')
    version_parser.add_argument('-M', '--major', help='Major version', action='store_true')
    version_parser.add_argument('-r', '--restore', help='Restore version', action='store_true')

    parser.add_argument('--dry-run', help='Dry run', action='store_true')

    return parser.parse_args()


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
            if line.startswith('__version__'):
                line = f"__version__ = '{version}'\n"
            if not line.endswith('\n'):
                line += '\n'
            file_data.append(line)
    [print(line, end='') for line in file_data]
    print()
    if not dry_run:
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
    # [print(line, end='') for line in file_data]
    if not dry_run:
        with open(toml_path, 'w') as f:
            f.writelines(file_data)


def undo_update(plugin: str) -> None:
    """Undo the update of a plugin."""
    staged_args = ['git', 'diff', '--cached', '--name-only']
    modified_args = ['git', 'diff', 'HEAD', '--name-only']
    staged = subprocess.check_output(staged_args, cwd=(PATH / plugin), text=True, stderr=subprocess.STDOUT).split('\n')
    modified = subprocess.check_output(modified_args, cwd=(PATH / plugin), text=True, stderr=subprocess.STDOUT).split('\n')
    version_files = ['pyproject.toml', Path('src') / plugin / '__init__.py']
    for version_file in version_files:
        if version_file in staged:
            args = ['git', 'restore', '--cached', version_file]
            with suppress(subprocess.CalledProcessError):
                subprocess.check_output(args, cwd=(PATH / plugin), text=True, stderr=subprocess.STDOUT)
        if version_file in modified:
            args = ['git', 'restore', version_file]
            with suppress(subprocess.CalledProcessError):
                subprocess.check_output(args, cwd=(PATH / plugin), text=True, stderr=subprocess.STDOUT)


def get_new_version(plugin: str, args: Namespace) -> tuple[str, str]:
    """Get the new version of a plugin."""
    actual_version = get_plugin_version(plugin)
    if args.patch:
        version = get_update_patch(plugin)
    elif args.minor:
        version = get_update_minor(plugin)
    elif args.major:
        version = get_update_major(plugin)
    else:
        version = args.version
    return actual_version, version


def check_if_update_needed(plugin: str) -> None:
    """Check if an update is needed."""
    args = ['git', 'diff', 'HEAD', '--name-only']
    output = subprocess.check_output(args, cwd=(PATH / plugin), text=True, stderr=subprocess.STDOUT)
    updated_files = []
    init = False
    for filename in output.split('\n'):
        if filename.startswith('src/') and filename.endswith('__init__.py'):
            init = True
            continue
        updated_files.append(filename)

    return updated_files and init

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


def main() -> None:
    """Run the main function."""
    args = parse_args()
    if not any([args.patch, args.minor, args.major, args.version, args.restore]):
        args.patch = True

    if args.plugin and args.plugin not in plugin_list:
        print(f'Plugin {args.plugin} not found')
        sys.exit(1)

    def update_plugin(plugin: str) -> None:
        actual_version, new_version = get_new_version(plugin, args)
        update_version(plugin, new_version, dry_run=args.dry_run)
        print(colorize('green',f'Updated {plugin} from {actual_version} to {new_version}'))

    datoso_version = get_datoso_version()

    if args.automatic or args.all:
        for plugin in plugin_list:
            undo_update(plugin)
            if args.restore:
                continue

            newfiles_args = ['git', 'ls-files', '--others', '--exclude-standard']
            modified_args = ['git', 'diff', 'HEAD', '--name-only']
            # ruff: noqa: ERA001, S603
            # args_updatedfiles = ["git", "ls-files", "--modified"]
            # args_stagedfiles = ["git", "diff", "--name-only", "--cached"]
            newfiles = subprocess.check_output(newfiles_args, cwd=(PATH / plugin), text=True, stderr=subprocess.STDOUT).split('\n')
            modified = subprocess.check_output(modified_args, cwd=(PATH / plugin), text=True, stderr=subprocess.STDOUT).split('\n')
            all_files = [x for x in newfiles + modified if x]
            if all_files or args.all:
                print(f'Plugin {colorize("cyan",plugin)}')
                print(colorize('yellow','Files:'))
                print(all_files)
                update_plugin(plugin)
    elif args.restore:
        undo_update(args.plugin)
    else:
        update_plugin(args.plugin)

    for plugin in plugin_list:
        if 'plugin' not in plugin or plugin == args.plugin:
            update_dependencies(plugin, datoso_version, plugin_list, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
