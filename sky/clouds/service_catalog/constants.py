"""Constants used for service catalog."""
import os

HOSTED_CATALOG_DIR_URL = 'https://raw.githubusercontent.com/skypilot-org/skypilot-catalog/master/catalogs'  # pylint: disable=line-too-long
CATALOG_SCHEMA_VERSION = 'v5'
LOCAL_CATALOG_DIR = os.path.expanduser('~/.sky/catalogs/')
