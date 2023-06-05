"""
OCI Configuration.
History:
 - Hysun He (hysun.he@oracle.com) @ Apr, 2023: Initial implementation
 
"""
import logging
import os
from sky import skypilot_config
from typing import Optional

logger = logging.getLogger(__name__)


class oci_conf:
    IMAGE_TAG_SPERATOR = "|"
    INSTANCE_TYPE_RES_SPERATOR = "$_"
    CPU_MEM_SPERATOR = "_"

    DEFAULT_NUM_VCPUS = 8
    DEFAULT_MEMORY_CPU_RATIO = 4

    VM_PREFIX = "VM.Standard"
    DEFAULT_INSTANCE_FAMILY = [
        # CPU: AMD, Memory: 8 GiB RAM per 1 vCPU;
        f"{VM_PREFIX}.E",
        # CPU: Intel, Memory: 8 GiB RAM per 1 vCPU;
        f"{VM_PREFIX}3",
        # CPU: ARM, Memory: 6 GiB RAM per 1 vCPU;
        # f'{VM_PREFIX}.A',
    ]

    COMPARTMENT = "skypilot_compartment"
    VCN_NAME = "skypilot_vcn"
    VCN_DNS_LABEL = "skypilotvcn"
    VCN_INTERNET_GATEWAY_NAME = "skypilot_vcn_internet_gateway"
    VCN_SUBNET_NAME = "skypilot_subnet"
    VCN_CIDR_INTERNET = "0.0.0.0/0"
    VCN_CIDR = "192.168.0.0/16"
    VCN_SUBNET_CIDR = "192.168.0.0/18"

    MAX_RETRY_COUNT = 3
    RETRY_INTERVAL_BASE_SECONDS = 5

    @classmethod
    def get_compartment(cls, region):
        # Allow task(cluster)-specific compartment/VCN parameters.
        default_compartment_ocid = skypilot_config.get_nested(
            ("oci", "default", "compartment_ocid"), None)
        compartment = skypilot_config.get_nested(
            ("oci", region, "compartment_ocid"), default_compartment_ocid)
        return compartment

    @classmethod
    def get_vcn_subnet(cls, region):
        vcn = skypilot_config.get_nested(("oci", region, "vcn_subnet"), None)
        return vcn

    @classmethod
    def get_default_gpu_image_tag(cls) -> str:
        # Get the default image tag (for gpu instances). Instead of hardcoding, we give a choice to set the
        # default image tag (for gpu instances) in the sky's user-config file (if not specified, use the hardcode
        # one at last)
        return skypilot_config.get_nested(("oci", "default", "image_tag_gpu"),
                                          "skypilot:gpu-ubuntu-2004")

    @classmethod
    def get_default_image_tag(cls) -> str:
        # Get the default image tag. Instead of hardcoding, we give a choice to set the default image tag
        # in the sky's user-config file. (if not specified, use the hardcode one at last)
        return skypilot_config.get_nested(
            ("oci", "default", "image_tag_general"), "skypilot:cpu-ubuntu-2004")

    @classmethod
    def get_sky_user_config_file(cls) -> str:
        config_path_via_env_var = os.environ.get(
            skypilot_config.ENV_VAR_SKYPILOT_CONFIG)
        if config_path_via_env_var is not None:
            config_path = config_path_via_env_var
        else:
            config_path = skypilot_config.CONFIG_PATH
        return config_path

    @classmethod
    def get_profile(cls) -> str:
        return skypilot_config.get_nested(
            ("oci", "default", "oci_config_profile"), "DEFAULT")
