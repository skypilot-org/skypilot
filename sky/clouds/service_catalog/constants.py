"""Constants used for service catalog."""
HOSTED_CATALOG_DIR_URL = 'https://skypilot-catalog.s3.us-east-1.amazonaws.com/catalogs'  # pylint: disable=line-too-long
CATALOG_SCHEMA_VERSION = 'v7'
CATALOG_DIR = '~/.sky/catalogs'
ALL_CLOUDS = ('aws', 'azure', 'gcp', 'ibm', 'lambda', 'scp', 'oci',
              'kubernetes', 'runpod', 'vast', 'vsphere', 'cudo', 'fluidstack',
              'paperspace', 'do', 'nebius')
