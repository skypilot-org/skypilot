"""OCI Configuration.
History:
 - Hysun He (hysun.he@oracle.com) @ Apr, 2023: Initial implementation
 - Zhanghao Wu @ Oct 2023: Formatting and refactoring
 - Hysun He (hysun.he@oracle.com) @ Oct, 2024: Add default image OS
   configuration.
"""
import logging
import os

from sky import skypilot_config
from sky.utils import resources_utils

logger = logging.getLogger(__name__)


class OCIConfig:
    """OCI Configuration."""
    IMAGE_TAG_SPERATOR = '|'
    INSTANCE_TYPE_RES_SPERATOR = '$_'
    CPU_MEM_SPERATOR = '_'

    DEFAULT_NUM_VCPUS = 8
    DEFAULT_MEMORY_CPU_RATIO = 4

    VM_PREFIX = 'VM.Standard'
    DEFAULT_INSTANCE_FAMILY = [
        # CPU: AMD, Memory: 8 GiB RAM per 1 vCPU;
        f'{VM_PREFIX}.E',
        # CPU: Intel, Memory: 8 GiB RAM per 1 vCPU;
        f'{VM_PREFIX}3',
        # CPU: ARM, Memory: 6 GiB RAM per 1 vCPU;
        # f'{VM_PREFIX}.A',
    ]

    COMPARTMENT = 'skypilot_compartment'
    VCN_NAME = 'skypilot_vcn'
    VCN_DNS_LABEL = 'skypilotvcn'
    VCN_INTERNET_GATEWAY_NAME = 'skypilot_vcn_internet_gateway'
    VCN_SUBNET_NAME = 'skypilot_subnet'
    VCN_CIDR_INTERNET = '0.0.0.0/0'
    VCN_CIDR = '192.168.0.0/16'
    VCN_SUBNET_CIDR = '192.168.0.0/18'

    MAX_RETRY_COUNT = 3
    RETRY_INTERVAL_BASE_SECONDS = 5

    # Map SkyPilot disk_tier to OCI VPU (Volume Performance Units) for Boot
    # Volume.
    # Tested with fio, bs=(R) 64.0KiB-64.0KiB, (W) 64.0KiB-64.0KiB,
    # (T) 64.0KiB-64.0KiB
    # -----------vpu=10------------
    # Use --numjobs=2 in fio command
    # read: IOPS=1932, BW=121MiB/s (127MB/s)(4096MiB/33904msec)
    # write: IOPS=2007, BW=125MiB/s (132MB/s)(4096MiB/32642msec)
    DISK_TIER_LOW = 10
    # -----------vpu=30------------
    # Use --numjobs=4 in fio command
    # read: IOPS=3104, BW=194MiB/s (203MB/s)(4096MiB/21113msec)
    # write: IOPS=3079, BW=192MiB/s (202MB/s)(4096MiB/21280msec)
    DISK_TIER_MEDIUM = 30
    # -----------vpu=90------------
    # Use --numjobs=8 in fio command
    # read: IOPS=5843, BW=365MiB/s (383MB/s)(8192MiB/22429msec)
    # write: IOPS=5833, BW=365MiB/s (382MB/s)(8192MiB/22469msec);
    # -----------vpu=100------------
    # Use --numjobs=8 in fio command
    # read: IOPS=6698, BW=419MiB/s (439MB/s)(8192MiB/19568msec)
    # write: IOPS=6707, BW=419MiB/s (440MB/s)(8192MiB/19540msec)
    DISK_TIER_HIGH = 100

    # disk_tier to OCI VPU mapping
    BOOT_VOLUME_VPU = {
        None: DISK_TIER_MEDIUM,  # Default to medium
        resources_utils.DiskTier.LOW: DISK_TIER_LOW,
        resources_utils.DiskTier.MEDIUM: DISK_TIER_MEDIUM,
        resources_utils.DiskTier.HIGH: DISK_TIER_HIGH,
    }

    @classmethod
    def get_compartment(cls, region):
        # Allow task(cluster)-specific compartment/VCN parameters.
        default_compartment_ocid = skypilot_config.get_nested(
            ('oci', 'default', 'compartment_ocid'), None)
        compartment = skypilot_config.get_nested(
            ('oci', region, 'compartment_ocid'), default_compartment_ocid)
        return compartment

    @classmethod
    def get_vcn_subnet(cls, region):
        vcn = skypilot_config.get_nested(('oci', region, 'vcn_subnet'), None)
        return vcn

    @classmethod
    def get_default_gpu_image_tag(cls) -> str:
        # Get the default image tag (for gpu instances). Instead of hardcoding,
        # we give a choice to set the default image tag (for gpu instances) in
        # the sky's user-config file (if not specified, use the hardcode one at
        # last)
        return skypilot_config.get_nested(('oci', 'default', 'image_tag_gpu'),
                                          'skypilot:gpu-ubuntu-2004')

    @classmethod
    def get_default_image_tag(cls) -> str:
        # Get the default image tag. Instead of hardcoding, we give a choice to
        # set the default image tag in the sky's user-config file. (if not
        # specified, use the hardcode one at last)
        return skypilot_config.get_nested(
            ('oci', 'default', 'image_tag_general'), 'skypilot:cpu-ubuntu-2004')

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
            ('oci', 'default', 'oci_config_profile'), 'DEFAULT')

    @classmethod
    def get_default_image_os(cls) -> str:
        # Get the default image OS. Instead of hardcoding, we give a choice to
        # set the default image OS type in the sky's user-config file. (if not
        # specified, use the hardcode one at last)
        return skypilot_config.get_nested(('oci', 'default', 'image_os_type'),
                                          'ubuntu')


oci_config = OCIConfig()
