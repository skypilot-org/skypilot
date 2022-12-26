"""Utility functions for TPUs."""
from typing import Optional

from sky import resources as resources_lib


def is_tpu(resources: Optional[resources_lib.Resources]) -> bool:
    if resources is None or resources.accelerators is None:
        return False
    acc, _ = list(resources.accelerators.items())[0]
    return acc.startswith('tpu')


def is_tpu_vm(resources: Optional[resources_lib.Resources]) -> bool:
    if resources is None or resources.accelerator_args is None:
        return False
    tpu_vm = resources.accelerator_args.get('tpu_vm', False)
    assert isinstance(tpu_vm, bool), tpu_vm
    return tpu_vm


def is_tpu_vm_pod(resources: Optional[resources_lib.Resources]) -> bool:
    if resources is None or not is_tpu_vm(resources):
        return False
    assert resources.accelerators is not None, resources
    acc, _ = list(resources.accelerators.items())[0]
    return acc not in ['tpu-v2-8', 'tpu-v3-8']


def get_num_tpu_devices(
        resources: Optional[resources_lib.Resources]) -> Optional[int]:
    if resources is None or not is_tpu(resources):
        return None
    assert resources.accelerators is not None, resources
    acc, _ = list(resources.accelerators.items())[0]
    num_tpu_devices = int(int(acc.split('-')[2]) / 8)
    return num_tpu_devices
