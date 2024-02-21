import os

from sky.constants import SKY_HOME

"""Constants used for service catalog."""
HOSTED_CATALOG_DIR_URL = 'https://raw.githubusercontent.com/skypilot-org/skypilot-catalog/master/catalogs'  # pylint: disable=line-too-long
CATALOG_SCHEMA_VERSION = 'v5'
CATALOG_DIR = os.path.expanduser(f'{SKY_HOME}/catalogs/')
ALL_CLOUDS = ('aws', 'azure', 'gcp', 'ibm', 'lambda', 'scp', 'oci',
              'kubernetes', 'runpod', 'vsphere', 'cudo', 'fluidstack',
              'paperspace')
