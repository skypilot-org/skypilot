"""Constants for service catalog."""
import os
from pathlib import Path

# The directory containing the service catalog files.
CATALOG_DIR = os.getenv('SKY_CATALOG_DIR',
                        str(Path.home() / '.sky' / 'catalogs' / 'v7'))

# The URL of the hosted catalog directory.
HOSTED_CATALOG_DIR_URL = 'https://raw.githubusercontent.com/skypilot-org/skypilot-catalog/master/catalogs/v7'

# The schema version of the catalog.
CATALOG_SCHEMA_VERSION = 'v7'

# All clouds supported by SkyPilot.
ALL_CLOUDS = [
    'aws',
    'azure',
    'gcp',
    'lambda',
    'cloudflare',
    'ibm',
    'scp',
    'oci',
    'runpod',
    'vsphere',
    'kubernetes',
    'fluidstack',
    'paperspace',
    'vast',
    'cudo',
    'do',
    'nebius',
    'hyperbolic',
]
