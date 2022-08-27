"""Utility functions for TPUs."""
from typing import Optional

from sky import resources as resources_lib


def is_tpu(resources: resources_lib.Resources) -> bool:
    if resources.accelerators is None:
        return False
    acc, _ = list(resources.accelerators.items())[0]
    return acc.startswith('tpu')


def is_tpu_vm(resources: resources_lib.Resources) -> bool:
    if resources.accelerator_args is None:
        return False
    return resources.accelerator_args.get('tpu_vm', False)


def is_tpu_vm_pod(resources: resources_lib.Resources) -> bool:
    if not is_tpu_vm(resources):
        return False
    acc, _ = list(resources.accelerators.items())[0]
    return acc not in ['tpu-v2-8', 'tpu-v3-8']


def get_num_tpu_devices(resources: resources_lib.Resources) -> Optional[int]:
    if not is_tpu(resources):
        return None
    acc, _ = list(resources.accelerators.items())[0]
    num_tpu_devices = int(int(acc.split('-')[2]) / 8)
    return num_tpu_devices
