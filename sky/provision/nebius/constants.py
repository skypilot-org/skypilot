"""Constants used by the Nebius provisioner."""
import math

VERSION = 'v1'

# Naming convention for SkyPilot-managed security groups. Mirrors AWS's
# `sky-sg-{cluster}` template (`USER_PORTS_SECURITY_GROUP_NAME` in
# `sky.clouds.aws`). Suffixed with `cluster_name_on_cloud` so SGs are
# unique per cluster within a project.
SECURITY_GROUP_TEMPLATE = 'sky-sg-{}'

# Nebius requires disk sizes to be a multiple of 93 GiB
# (99857989632 bytes = 93 * 1024^3).
NEBIUS_DISK_SIZE_STEP_GIB = 93


def round_up_disk_size(disk_size_gib: int) -> int:
    """Round up disk size to the nearest multiple of 93 GiB."""
    return (math.ceil(disk_size_gib / NEBIUS_DISK_SIZE_STEP_GIB) *
            NEBIUS_DISK_SIZE_STEP_GIB)


# InfiniBand-capable instance platforms
INFINIBAND_INSTANCE_PLATFORMS = [
    'gpu-h100-sxm',
    'gpu-h200-sxm',
    'gpu-b200-sxm',
    # me-west1 exposes the B200 platform under a distinct name.
    'gpu-b200-sxm-a',
    'gpu-b300-sxm',
]

# Accelerators whose 8-GPU instances can be grouped into an InfiniBand GPU
# cluster. Kept in sync with INFINIBAND_INSTANCE_PLATFORMS, which names the
# same hardware as Nebius platform strings.
INFINIBAND_ACCELERATORS = frozenset({'h100', 'h200', 'b200', 'b300'})

# Only 8-GPU VMs can be grouped into a GPU cluster. The vCPU/memory part of the
# preset differs per platform (e.g. '8gpu-128vcpu-1600gb' for H100/H200,
# '8gpu-192vcpu-2768gb' for B300), so match on the GPU count alone.
INFINIBAND_PRESET_PREFIX = '8gpu-'
INFINIBAND_GPU_COUNT = 8

# InfiniBand environment variables for NCCL and UCX
INFINIBAND_ENV_VARS = {
    'NCCL_IB_HCA': 'mlx5',
    'UCX_NET_DEVICES': ('mlx5_0:1,mlx5_1:1,mlx5_2:1,mlx5_3:1,'
                        'mlx5_4:1,mlx5_5:1,mlx5_6:1,mlx5_7:1')
}

# pylint: disable=line-too-long
INFINIBAND_IMAGE_ID = 'docker:cr.eu-north1.nebius.cloud/nebius-benchmarks/nccl-tests:2.23.4-ubu22.04-cu12.4'

# Docker run options for InfiniBand support
INFINIBAND_DOCKER_OPTIONS = ['--device=/dev/infiniband', '--cap-add=IPC_LOCK']

# InfiniBand fabric mapping by platform and region
# Based on Nebius documentation
# https://docs.nebius.com/compute/clusters/gpu#fabrics
INFINIBAND_FABRIC_MAPPING = {
    # H100 platforms
    ('gpu-h100-sxm', 'eu-north1'): [
        'fabric-2', 'fabric-3', 'fabric-4', 'fabric-6'
    ],

    # H200 platforms
    ('gpu-h200-sxm', 'eu-north1'): ['fabric-7'],
    ('gpu-h200-sxm', 'eu-west1'): ['fabric-5'],
    ('gpu-h200-sxm', 'us-central1'): ['us-central1-a'],

    # B200 platforms
    ('gpu-b200-sxm', 'us-central1'): ['us-central1-b'],
    ('gpu-b200-sxm-a', 'me-west1'): ['me-west1-a'],

    # B300 platforms
    ('gpu-b300-sxm', 'uk-south1'): ['uk-south1-a'],
}


def get_default_fabric(platform: str, region: str) -> str:
    """Get the default (first) fabric for a given platform and region.

    Raises:
        ValueError: if the platform is not offered in the region. A fabric is
            tied to a specific platform in a specific region, so there is no
            usable default to fall back on; the caller is expected to report
            this and let the user set one explicitly.
    """
    fabrics = INFINIBAND_FABRIC_MAPPING.get((platform, region), [])
    if not fabrics:
        raise ValueError(f'No InfiniBand fabric available for platform '
                         f'{platform} in region {region}')
    return fabrics[0]
