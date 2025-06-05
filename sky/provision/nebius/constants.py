"""Constants used by the Nebius provisioner."""
import textwrap

VERSION = 'v1'

# InfiniBand environment variables for NCCL and UCX
INFINIBAND_ENV_VARS = {
    'NCCL_IB_HCA': 'mlx5',
    'UCX_NET_DEVICES': 'mlx5_0:1,mlx5_1:1,mlx5_2:1,mlx5_3:1,mlx5_4:1,mlx5_5:1,mlx5_6:1,mlx5_7:1'
}

# Docker run options for InfiniBand support
INFINIBAND_DOCKER_OPTIONS = [
    '--device=/dev/infiniband',
    '--cap-add=IPC_LOCK'
]

