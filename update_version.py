#!/usr/bin/env python3
import subprocess
import sys
from argparse import ArgumentParser
from contextlib import suppress
from pathlib import Path

from config import PATH
from pip._vendor.pygments.console import colorize

plugin_list = [
    'datoso',
    'datoso_plugin_internetarchive',
    'datoso_seed_base',
    'datoso_seed_fbneo',
    'datoso_seed_md_enhanced',
    'datoso_seed_nointro',
    'datoso_seed_pleasuredome',
    'datoso_seed_private',
    'datoso_seed_redump',
    'datoso_seed_sfc_enhancedcolors',
    'datoso_seed_sfc_msu1',
    'datoso_seed_sfc_speedhacks',
    'datoso_seed_tdc',
    'datoso_seed_translatedenglish',
    'datoso_seed_vpinmame',
    'datoso_seed_whdload',
]

def get_datoso_version():
    get_plugin_version('datoso')

def get_plugin_version(plugin):
    plugin_path = PATH / plugin / 'src' / plugin / '__init__.py'
    with open(plugin_path) as f:
        for line in f.readlines():
            if line.strip().startswith('__version__'):
                return line.split('=')[1].strip().replace('"', '')
    return None

def get_plugin_versions():
    plugins = {}
    for plugin in plugin_list:
        plugins[plugin] = {
            'name': plugin,
            'version': get_plugin_version(plugin),
        }
    return plugins

def update_version(plugin, version, dry_run=False):
    plugin_path = PATH / plugin / 'src' / plugin
    file_data = []
    file_path = Path(plugin_path) / '__init__.py'
    with open(file_path) as f:
        for line in f.readlines():
            if line.startswith('__version__'):
                line = f'__version__ = "{version}"\n'
            if not line.endswith('\n'):
                line += '\n'
            file_data.append(line)
    [print(line, end='') for line in file_data]
    print()
    if not dry_run:
        with open(file_path, 'w') as f:
            f.writelines(file_data)

def update_dependencies(plugin, datoso_version, plugins, dry_run=False):
    toml_path = PATH / plugin / 'pyproject.toml'
    file_data = []
    # ruff: noqa: PLW2901
    with open(toml_path) as f:
        for line in f.readlines():
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

def parse_args():
    """Parse arguments."""
    parser = ArgumentParser(description='Update the version of a plugin and seed')

    plugin_parser = parser.add_mutually_exclusive_group(required=True)
    plugin_parser.add_argument('--plugin', help='Plugin Name')
    plugin_parser.add_argument('-a', '--automatic', help='Bump versions of all plugins', action='store_true')


    version_parser = parser.add_mutually_exclusive_group(required=True)
    version_parser.add_argument('-v', '--version', help='New version')
    version_parser.add_argument('-d', '--dev', help='Developer version', action='store_true')
    version_parser.add_argument('-p', '--patch', help='Patch version', action='store_true')
    version_parser.add_argument('-m', '--minor', help='Minor version', action='store_true')
    version_parser.add_argument('-M', '--major', help='Major version', action='store_true')
    version_parser.add_argument('-r', '--restore', help='Restore version', action='store_true')

    parser.add_argument('--dry-run', help='Dry run', action='store_true')

    return parser.parse_args()

def main():
    args = parse_args()
    if not any([args.patch, args.minor, args.major, args.version, args.restore]):
        args.patch = True

    if args.plugin and args.plugin not in plugin_list:
        print(f'Plugin {args.plugin} not found')
        sys.exit(1)

    def update_plugin(plugin):
        actual_version, new_version = get_new_version(plugin, args)
        update_version(plugin, new_version, args.dry_run)
        print(colorize('green',f'Updated {plugin} from {actual_version} to {new_version}'))

    datoso_version = get_datoso_version()

    if args.automatic:
        for plugin in plugin_list:
            undo_update(plugin)
            if args.restore:
                continue

            newfiles_args = ['git', 'ls-files', '--others', '--exclude-standard']
            modified_args = ['git', 'diff', 'HEAD', '--name-only']
            # ruff: noqa: ERA001
            # args_updatedfiles = ["git", "ls-files", "--modified"]
            # args_stagedfiles = ["git", "diff", "--name-only", "--cached"]
            newfiles = subprocess.check_output(newfiles_args, cwd=(PATH / plugin), text=True, stderr=subprocess.STDOUT).split('\n')
            modified = subprocess.check_output(modified_args, cwd=(PATH / plugin), text=True, stderr=subprocess.STDOUT).split('\n')
            all_files = [x for x in newfiles + modified if x]
            if all_files:
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
            update_dependencies(plugin, datoso_version, plugin_list, args.dry_run)


def undo_update(plugin):
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

def get_new_version(plugin, args):
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

def check_if_update_needed(plugin):
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

def get_update_patch(plugin):
    version = get_plugin_version(plugin)
    version = version.split('.')
    version[2] = str(int(version[2]) + 1)
    return '.'.join(version)

def get_update_minor(plugin):
    version = get_plugin_version(plugin)
    version = version.split('.')
    version[1] = str(int(version[1]) + 1)
    version[2] = '0'
    return '.'.join(version)

def get_update_major(plugin):
    version = get_plugin_version(plugin)
    version = version.split('.')
    version[0] = str(int(version[0]) + 1)
    version[1] = '0'
    version[2] = '0'
    return '.'.join(version)


if __name__ == '__main__':
    main()
