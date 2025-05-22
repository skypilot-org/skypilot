"""Constants used for service catalog."""
HOSTED_CATALOG_DIR_URL = 'https://raw.githubusercontent.com/skypilot-org/skypilot-catalog/master/catalogs'  # pylint: disable=line-too-long
HOSTED_CATALOG_DIR_URL_S3_MIRROR = 'https://skypilot-catalog.s3.us-east-1.amazonaws.com/catalogs'  # pylint: disable=line-too-long
CATALOG_SCHEMA_VERSION = 'v7'
CATALOG_DIR = '~/.sky/catalogs'
ALL_CLOUDS = ('aws', 'azure', 'gcp', 'ibm', 'lambda', 'scp', 'oci',
              'kubernetes', 'runpod', 'vast', 'vsphere', 'cudo', 'fluidstack',
              'paperspace', 'do', 'nebius', 'ssh')
