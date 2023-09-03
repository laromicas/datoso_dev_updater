import os
import sys
from argparse import ArgumentParser

path = '/home/laromicas/datoso_dev'
plugins_path = '/home/laromicas/datoso_dev'

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
    'datoso_seed_translatedenglish',
    'datoso_seed_vpinmame',
    # 'datoso_seed_whdload',
]

def get_datoso_version():
    get_plugin_version('datoso')

def get_plugin_version(plugin):
    plugin_path = os.path.join(path, plugin, 'src', plugin, '__init__.py')
    with open(plugin_path, 'r') as f:
        for line in f.readlines():
            if line.strip().startswith('__version__'):
                version = line.split('=')[1].strip().replace('"', '')
                return version

def get_plugin_versions():
    plugins = {}
    for plugin in plugin_list:
        plugins[plugin] = {
            'name': plugin,
            'version': get_plugin_version(plugin),
        }
    return plugins

def update_version(plugin, version, dry_run=False):
    plugin_path = os.path.join(path, plugin, 'src', plugin)
    file_data = []
    file_path = os.path.join(plugin_path, '__init__.py')
    with open(file_path, 'r') as f:
        for line in f.readlines():
            if line.startswith('__version__'):
                line = f'__version__ = "{version}"\n'
            if not line.endswith('\n'):
                line += '\n'
            file_data.append(line)
    [print(line, end='') for line in file_data]
    if not dry_run:
        with open(file_path, 'w') as f:
            f.writelines(file_data)

def update_dependencies(plugin, datoso_version, plugins, dry_run=False):
    toml_path = os.path.join(path, plugin, 'pyproject.toml')
    file_data = []
    with open(toml_path, 'r') as f:
        for line in f.readlines():
            if not line.endswith('\n'):
                line += '\n'
            if 'datoso>' in line.strip():
                line = f'    "datoso>={datoso_version}",\n'
            for plugin in plugins.values():
                plugin_name = plugin['name'].replace('_','-')
                if line.strip().startswith(f'"{plugin_name}>'):
                    line = f'    "{plugin_name}>={plugin["version"]}",\n'
                package = plugin_name.split('-')[-1]
                if line.strip().startswith(f'{package} ='):
                    line = f'{package} = [ "{plugin_name}>={plugin["version"]} ]"\n'
            file_data.append(line)
    # [print(line, end='') for line in file_data]
    if not dry_run:
        with open(toml_path, 'w') as f:
            f.writelines(file_data)

def parse_args():
    """ Parse arguments. """
    parser = ArgumentParser(description='Update the version of a plugin and seed')
    parser.add_argument('plugin', help='Plugin Name')
    version_parser = parser.add_mutually_exclusive_group(required=True)
    version_parser.add_argument('-v', '--version', help='New version')
    version_parser.add_argument('-p', '--patch', help='Patch version', action='store_true')
    version_parser.add_argument('-m', '--minor', help='Minor version', action='store_true')
    version_parser.add_argument('-M', '--major', help='Major version', action='store_true')
    version_parser.add_argument('--dry-run', help='Dry run', action='store_true')
    return parser.parse_args()

def main():
    args = parse_args()
    if args.plugin not in plugin_list:
        print(f'Plugin {args.plugin} not found')
        sys.exit(1)

    if args.patch:
        version = update_patch(args)
    elif args.minor:
        version = update_minor(args)
    elif args.major:
        version = update_major(args)
    else:
        version = args.version
    update_version(args.plugin, version)
    print(f'Updated {args.plugin} from {get_plugin_version(args.plugin)} to {version}')


    datoso_version = get_datoso_version()
    plugins = get_plugin_versions()
    print(plugins)
    plugins[args.plugin]['version'] = version
    print(plugins)
    for plugin in plugin_list:
        update_dependencies(plugin, datoso_version, plugins)


def update_patch(args):
    version = get_plugin_version(args.plugin)
    version = version.split('.')
    version[2] = str(int(version[2]) + 1)
    version = '.'.join(version)
    return version

def update_minor(args):
    version = get_plugin_version(args.plugin)
    version = version.split('.')
    version[1] = str(int(version[1]) + 1)
    version[2] = '0'
    version = '.'.join(version)
    return version

def update_major(args):
    version = get_plugin_version(args.plugin)
    version = version.split('.')
    version[0] = str(int(version[0]) + 1)
    version[1] = '0'
    version[2] = '0'
    version = '.'.join(version)
    return version


if __name__ == '__main__':
    main()