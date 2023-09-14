"""Constants used for service catalog."""
import os

HOSTED_CATALOG_DIR_URL = 'https://raw.githubusercontent.com/romilbhardwaj/skypilot-catalog/k8s_images/catalogs'  # TODO - REVERT AFTER CATALOG PR IS MERGED. pylint: disable=line-too-long
CATALOG_SCHEMA_VERSION = 'v5'
LOCAL_CATALOG_DIR = os.path.expanduser('~/.sky/catalogs/')
