"""Constants used for service catalog."""
HOSTED_CATALOG_DIR_URL = 'https://raw.githubusercontent.com/skypilot-org/skypilot-catalog/master/catalogs'  # pylint: disable=line-too-long
CATALOG_SCHEMA_VERSION = 'v5'
CATALOG_DIR = '~/.sky/catalogs'
ALL_CLOUDS = ('aws', 'azure', 'gcp', 'ibm', 'lambda', 'scp', 'oci',
              'kubernetes', 'runpod', 'vsphere', 'cudo', 'fluidstack',
              'paperspace')
# Azure has those fractional A10 instance types, which still shows has 1 A10 GPU
# in the API response. We manually changing the number of GPUs to a float here.
AZURE_FRACTIONAL_A10_INS_TYPE_TO_NUM_GPUS = {
    f'Standard_NV{vcpu}ads_A10_v5': vcpu / 24 for vcpu in [6, 12, 18]
}
