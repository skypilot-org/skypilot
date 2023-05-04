"""
OCI Configuration.
History:
 - Hysun He (hysun.he@oracle.com) @ Apr, 2023: Initial implementation
 
"""
import oci
import os
import logging
from sky import skypilot_config

logger = logging.getLogger(__name__)

class oci_conf:
    ENV_VAR_OCI_CONFIG = 'OCI_CONFIG'
    CONFIG_PATH = '~/.oci/config'
    IMAGE_TAG_SPERATOR = '|'
    INSTANCE_TYPE_RES_SPERATOR = '$_'

    _DEFAULT_NUM_VCPUS = 2
    _DEFAULT_MEMORY_CPU_RATIO = 6

    _VM_PREFIX = 'VM.Standard'
    _DEFAULT_INSTANCE_FAMILY = [
        # CPU: AMD, Memory: 8 GiB RAM per 1 vCPU;
        f'{_VM_PREFIX}.E',
        # CPU: Intel, Memory: 8 GiB RAM per 1 vCPU;
        f'{_VM_PREFIX}3',
        # CPU: ARM, Memory: 6 GiB RAM per 1 vCPU;
        # f'{_VM_PREFIX}.A',
    ]

    MAX_RETRY_COUNT = 3
    RETRY_INTERVAL_BASE_SECONDS = 5

    conf_file_path = CONFIG_PATH
    config_path_via_env_var = os.environ.get(ENV_VAR_OCI_CONFIG)
    if config_path_via_env_var is not None:
        conf_file_path = config_path_via_env_var

    oci_config = oci.config.from_file(file_location = conf_file_path)
    core_client = oci.core.ComputeClient(oci_config)
    net_client = oci.core.VirtualNetworkClient(oci_config)
    search_client = oci.resource_search.ResourceSearchClient(oci_config)
    identity_client = oci.identity.IdentityClient(oci_config)


    @classmethod
    def get_compartment(cls, cluster_name):
        # Allow task(cluster)-specific compartment/VCN parameters.
        defval = skypilot_config.get_nested(('oci', 'default', 'compartment_ocid'), None) 
        compartment = skypilot_config.get_nested(('oci', cluster_name, 'compartment_ocid'), defval)
        return compartment


    @classmethod
    def get_vcn(cls, cluster_name):
        # Allow task(cluster)-specific compartment/VCN parameters.
        defval = skypilot_config.get_nested(('oci', 'default', 'vcn_ocid'), None) 
        vcn = skypilot_config.get_nested(('oci', cluster_name, 'vcn_ocid'), defval)
        return vcn
    

    @classmethod
    def get_default_gpu_image_tag(cls) -> str:
        # Get the default image tag (for gpu instances). Instead of hardcoding, we give a choice to set the
        # default image tag (for gpu instances) in the sky's user-config file (if not specified, use the hardcode
        # one at last)
        return skypilot_config.get_nested(('oci', 'default', 'image_tag_gpu'), 'skypilot:oci-ubuntu-NVIDIA-VMI-20_04')
    
    
    @classmethod
    def get_default_image_tag(cls) -> str:
        # Get the default image tag. Instead of hardcoding, we give a choice to set the default image tag 
        # in the sky's user-config file. (if not specified, use the hardcode one at last)
        return skypilot_config.get_nested(('oci', 'default', 'image_tag_general'), 'skypilot:oci-ubuntu-20_04')