"""Constants used by the Nebius provisioner."""

VERSION = 'v1'

# InfiniBand-capable instance platforms
INFINIBAND_INSTANCE_PLATFORMS = [
    'gpu-h100-sxm',
    'gpu-h200-sxm',
]

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
INFINIBAND_FABRIC_MAPPING = {
    # H100 platforms
    ('gpu-h100-sxm', 'eu-north1'): [
        'fabric-2', 'fabric-3', 'fabric-4', 'fabric-6'
    ],

    # H200 platforms
    ('gpu-h200-sxm', 'eu-north1'): ['fabric-7'],
    ('gpu-h200-sxm', 'eu-west1'): ['fabric-5'],
    ('gpu-h200-sxm', 'us-central1'): ['us-central1-a'],
}


def get_default_fabric(platform: str, region: str) -> str:
    """Get the default (first) fabric for a given platform and region."""
    fabrics = INFINIBAND_FABRIC_MAPPING.get((platform, region), [])
    if not fabrics:
        # Select north europe region as default
        fabrics = INFINIBAND_FABRIC_MAPPING.get(('gpu-h100-sxm', 'eu-north1'),
                                                [])
        if not fabrics:
            raise ValueError(
                f'No InfiniBand fabric available for platform {platform} '
                f'in region {region}')
    return fabrics[0]
