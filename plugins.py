"""Plugin version checker."""
from packaging.version import Version

from config import PATH

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