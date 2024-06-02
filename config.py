from configparser import ConfigParser
from pathlib import Path

config = ConfigParser()
config.read('config.ini')
PATH = Path(config.get('DEFAULT', 'PATH'))
TOKEN = config.get('GITHUB', 'TOKEN')
OWNER = config.get('GITHUB', 'OWNER')
