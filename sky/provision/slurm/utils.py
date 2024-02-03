"""Slurm utilities for SkyPilot."""

from sky.provision import utils as provision_utils

SlurmInstanceType = provision_utils.VirtualInstanceType


def get_slurm_gpu_name(gpu_type: str) -> str:
    """Returns the GPU name on slurm cluster of the given GPU type."""
    return f'gpu:{gpu_type}'
