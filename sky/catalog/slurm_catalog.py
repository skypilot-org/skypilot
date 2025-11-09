"""Slurm Catalog."""

from typing import Optional

from sky import sky_logging
from sky.utils import resources_utils

logger = sky_logging.init_logger(__name__)


_DEFAULT_NUM_VCPUS = 2
_DEFAULT_MEMORY_CPU_RATIO = 1


def get_default_instance_type(cpus: Optional[str] = None,
                              memory: Optional[str] = None,
                              disk_tier: Optional[
                                  resources_utils.DiskTier] = None,
                              region: Optional[str] = None,
                              zone: Optional[str] = None) -> Optional[str]:
    from sky.provision.slurm import utils as slurm_utils

    # Delete unused disk_tier.
    del disk_tier

    # Slurm can provision resources through options like --cpus-per-task and --mem.
    instance_cpus = float(
        cpus.strip('+')) if cpus is not None else _DEFAULT_NUM_VCPUS
    if memory is not None:
        if memory.endswith('+'):
            instance_mem = float(memory[:-1])
        elif memory.endswith('x'):
            instance_mem = float(memory[:-1]) * instance_cpus
        else:
            instance_mem = float(memory)
    else:
        instance_mem = instance_cpus * _DEFAULT_MEMORY_CPU_RATIO
    virtual_instance_type = slurm_utils.SlurmInstanceType(
        instance_cpus, instance_mem).name
    return virtual_instance_type
