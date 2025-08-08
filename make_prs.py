#!/usr/bin/env python3
"""Update the version of datoso plugins and seeds."""
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path

from lib.git import (
    add_files_to_stage,
    check_if_branch_exists,
    checkout_branch,
    commit_all,
    create_branch,
    get_all_files,
    get_branch,
)
from plugins import get_datoso_version, get_plugin_version, plugin_list

# ruff: noqa: E501, C901

def parse_args() -> Namespace:
    """Parse arguments."""
    parser = ArgumentParser(description='Update the version of a plugin and seed')

    plugin_parser = parser.add_mutually_exclusive_group(required=True)
    plugin_parser.add_argument('--plugin', help='Plugin Name')
    plugin_parser.add_argument('-a', '--automatic', help='Bump versions of all plugins', action='store_true')
    # plugin_parser.add_argument('-A', '--all', help='Bump versions of all plugins', action='store_true')


    parser.add_argument('-pr', '--pull-request', help='Create pull request', action='store_true')
    parser.add_argument('-b', '--branch', help='Create Branch', default='master')
    parser.add_argument('-pb', '--push-branch', help='Push Branch', action='store_true')
    message_parser = parser.add_mutually_exclusive_group(required=False)
    message_parser.add_argument('-m', '--message', help='Commit message')
    message_parser.add_argument('-am', '--auto-message', help='Commit message', action='store_true')

    parser.add_argument('--dry-run', help='Dry run', action='store_true')

    return parser.parse_args()



def main() -> None:
    """Run the main function."""
    args = parse_args()

    #temporal while testing
    args.dry_run = True

    if args.plugin and args.plugin not in plugin_list:
        print(f'Plugin {args.plugin} not found')
        sys.exit(1)

    datoso_version = get_datoso_version()

    if args.automatic:
        plugins = plugin_list
    if args.plugin:
        plugins = [args.plugin]

    for plugin in plugins:
        modified_files = get_all_files(plugin)
        if not modified_files \
            or modified_files == ['pyproject.toml'] \
            or modified_files == ['pyproject.toml', str(Path('src') / plugin / '__init__.py')]:
            continue
        version = get_plugin_version(plugin)
        branch = get_branch(plugin)
        if str(Path('src') / plugin / '__init__.py') not in modified_files:
            if str(version) == branch:
                print(f'{plugin} is on the same branch as the current version, do you want to add new files to PR?')
                if input('y/n: ').lower() != 'y':
                    continue
            else:
                #TODO: check if the branch exists and change branch to it
                print(f'{plugin} does not seem to be in the same branch as the current version, do you really want to create a PR?')
                if input('y/n: ').lower() != 'y':
                    continue
        if str(version) != branch:
            if not check_if_branch_exists(plugin, version):
                create_branch(plugin, str(version))
            else:
                checkout_branch(plugin, version)

        print(f'{plugin} was updated with version {version}, do you want to create a pull request?')
        if input('y/n: ').lower() != 'y':
            continue
        add_files_to_stage(plugin)
        if args.auto_message:
            commit_message = f'Update {plugin} version to {version}'
        else:
            commit_message = args.message
            if not commit_message:
                commit_message = input('Please provide a commit message:')
        commit_all(plugin, commit_message)


    #         newfiles_args = ['git', 'ls-files', '--others', '--exclude-standard']
    #         modified_args = ['git', 'diff', 'HEAD', '--name-only']
    #         # ruff: noqa: ERA001, S603
    #         # args_updatedfiles = ["git", "ls-files", "--modified"]
    #         # args_stagedfiles = ["git", "diff", "--name-only", "--cached"]
    #         newfiles = subprocess.check_output(newfiles_args, cwd=(PATH / plugin), text=True, stderr=subprocess.STDOUT).split('\n')
    #         modified = subprocess.check_output(modified_args, cwd=(PATH / plugin), text=True, stderr=subprocess.STDOUT).split('\n')
    #         all_files = [x for x in newfiles + modified if x]
    #         if all_files or args.all:
    #             print(f'Plugin {colorize("cyan",plugin)}')
    #             print(colorize('yellow','Files:'))
    #             print(all_files)
    #             update_plugin(plugin)
    # elif args.restore:
    #     undo_update(args.plugin)
    # else:
    #     update_plugin(args.plugin)

    # for plugin in plugin_list:
    #     if 'plugin' not in plugin or plugin == args.plugin:
    #         update_dependencies(plugin, datoso_version, plugin_list, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
