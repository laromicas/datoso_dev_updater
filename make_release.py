#!/usr/bin/env python3
import sys
from argparse import ArgumentParser

import requests
from config import config
from packaging.version import Version
from plugins import get_plugin_version, plugin_list

# ruff: noqa: ERA001, E501

plugin_version_cache = {}

def parse_args():
    """Parse arguments."""
    parser = ArgumentParser(description='Create a Release')
    parser.add_argument('--token', help='GitHub Token', default=config.get('GITHUB', 'TOKEN', fallback=''),
                        required=not (config.get('GITHUB', 'TOKEN', fallback='')))
    parser.add_argument('--owner', help='Owner', default=config.get('GITHUB', 'OWNER', fallback='laromicas'))
    parser.add_argument('--branch', help='Branch', default='master')

    plugin_parser = parser.add_mutually_exclusive_group(required=True)
    plugin_parser.add_argument('--plugin', help='Plugin Name, repo name')
    plugin_parser.add_argument('-a', '--automatic', help='Create Releases for all plugins that have a new version',
                               action='store_true')
    plugin_parser.add_argument('-A', '--all', help='Create Releases for all plugins', action='store_true')

    parser.add_argument('-p', '--prerelease', help='Pre-release version', action='store_true')

    release_parser = parser.add_mutually_exclusive_group(required=True)
    release_parser.add_argument('-l', '--latest', help='Make latest version', action='store_true')
    release_parser.add_argument('-d', '--draft', help='Draft release', action='store_true')

    parser.add_argument('--dry-run', help='Dry run', action='store_true')

    return parser.parse_args()


def get_release_version(args, plugin):
    """get latest version from GitHub"""
    # global plugin_version_cache
    if plugin in plugin_version_cache:
        return plugin_version_cache[plugin]
    url = f'https://api.github.com/repos/{args.owner}/{plugin}/releases'
    headers = {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {args.token}',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()
    data = response.json()
    version = Version(data[0]['tag_name'])
    plugin_version_cache[plugin] = version
    return version

def create_release(args, plugin):
    """Create a new release"""
    url = f'https://api.github.com/repos/{args.owner}/{plugin}/releases'
    headers = {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {args.token}',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    data = {
        'tag_name': f'v{get_plugin_version(plugin)}',
        'target_commitish': args.branch,
        'name': f'v{get_plugin_version(plugin)}',
        'body': 'Description of the release',
        'draft': args.draft,
        'prerelease': args.prerelease or get_plugin_version(plugin).is_prerelease,
        'generate_release_notes': False,
        'make_latest': args.latest,
    }
    print(f'It will create a release with the following data for {plugin}:')
    print(data)
    if input('Do you want to continue? [y/N] ').lower() != 'y':
        sys.exit(1)
    response = requests.post(url, headers=headers, json=data, timeout=10)
    response.raise_for_status()
    return response.json()

def is_new_version_valid(args, plugin):
    """Validate version"""
    new_version = get_plugin_version(plugin)
    current_version = get_release_version(args, plugin)
    # if new_version <= current_version:
    #     raise ValueError(f'New version {new_version} is less than or equal to the current version {current_version}')
    return new_version > current_version

if __name__ == '__main__':
    args = parse_args()
    plugins = list(plugin_list) if args.automatic or args.all else [args.plugin]

    for plugin in plugins:
        if is_new_version_valid(args, plugin):
            create_release(args, plugin)
        else:
            print(f'New version is not valid for {plugin}')
    print('Done')
