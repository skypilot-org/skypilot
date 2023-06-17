"""Constants used for service catalog."""
import os

# TODO (Hriday Sheth) change URL once catalog branch is merged in
HOSTED_CATALOG_DIR_URL = 'https://raw.githubusercontent.com/shethhriday29/skypilot-catalog/master/catalogs'  # pylint: disable=line-too-long
CATALOG_SCHEMA_VERSION = 'v5'
LOCAL_CATALOG_DIR = os.path.expanduser('~/.sky/catalogs/')
