"""Constants used for service catalog."""
import os

HOSTED_CATALOG_DIR_URL = 'https://raw.githubusercontent.com/skypilot-org/skypilot-catalog/master/catalogs'  # pylint: disable=line-too-long
CATALOG_SCHEMA_VERSION = 'v5'
CATALOG_REL_DIR = '~/.sky/catalogs'
LOCAL_CATALOG_DIR = os.path.expanduser(CATALOG_REL_DIR)
