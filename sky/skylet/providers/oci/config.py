"""
OCI Configuration.
History:
 - Hysun He (hysun.he@oracle.com) @ Apr, 2023: Initial implementation
 
"""
import os
import logging
from sky import skypilot_config
from sky.adaptors import oci

logger = logging.getLogger(__name__)


class oci_conf:
    IMAGE_TAG_SPERATOR = "|"
    INSTANCE_TYPE_RES_SPERATOR = "$_"

    DEFAULT_NUM_VCPUS = 2
    DEFAULT_MEMORY_CPU_RATIO = 6

    VM_PREFIX = "VM.Standard"
    DEFAULT_INSTANCE_FAMILY = [
        # CPU: AMD, Memory: 8 GiB RAM per 1 vCPU;
        f"{VM_PREFIX}.E",
        # CPU: Intel, Memory: 8 GiB RAM per 1 vCPU;
        f"{VM_PREFIX}3",
        # CPU: ARM, Memory: 6 GiB RAM per 1 vCPU;
        # f'{VM_PREFIX}.A',
    ]

    MAX_RETRY_COUNT = 3
    RETRY_INTERVAL_BASE_SECONDS = 5

    oci_config = oci.get_oci_config()
    core_client = oci.get_core_client()
    net_client = oci.get_net_client()
    search_client = oci.get_search_client()
    identity_client = oci.get_identity_client()

    @classmethod
    def get_compartment(cls, cluster_name):
        # Allow task(cluster)-specific compartment/VCN parameters.
        defval = skypilot_config.get_nested(
            ("oci", "default", "compartment_ocid"), None
        )
        compartment = skypilot_config.get_nested(
            ("oci", cluster_name, "compartment_ocid"), defval
        )
        return compartment

    @classmethod
    def get_vcn(cls, cluster_name):
        # Allow task(cluster)-specific compartment/VCN parameters.
        defval = skypilot_config.get_nested(("oci", "default", "vcn_ocid"), None)
        vcn = skypilot_config.get_nested(("oci", cluster_name, "vcn_ocid"), defval)
        return vcn

    @classmethod
    def get_default_gpu_image_tag(cls) -> str:
        # Get the default image tag (for gpu instances). Instead of hardcoding, we give a choice to set the
        # default image tag (for gpu instances) in the sky's user-config file (if not specified, use the hardcode
        # one at last)
        return skypilot_config.get_nested(
            ("oci", "default", "image_tag_gpu"), "skypilot:oci-ubuntu-NVIDIA-VMI-20_04"
        )

    @classmethod
    def get_default_image_tag(cls) -> str:
        # Get the default image tag. Instead of hardcoding, we give a choice to set the default image tag
        # in the sky's user-config file. (if not specified, use the hardcode one at last)
        return skypilot_config.get_nested(
            ("oci", "default", "image_tag_general"), "skypilot:oci-ubuntu-20_04"
        )
