"""Utility functions for TPUs."""
import typing
from typing import Optional

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib


def is_tpu(resources: Optional['resources_lib.Resources']) -> bool:
    if resources is None or resources.accelerators is None:
        return False
    acc, _ = list(resources.accelerators.items())[0]
    return acc.startswith('tpu')


def is_tpu_vm(resources: Optional['resources_lib.Resources']) -> bool:
    if not is_tpu(resources):
        return False
    assert resources is not None
    if resources.accelerator_args is None:
        return True
    return resources.accelerator_args.get('tpu_vm', True)


def is_tpu_vm_pod(resources: Optional['resources_lib.Resources']) -> bool:
    if not is_tpu_vm(resources):
        return False
    assert resources is not None
    acc, _ = list(resources.accelerators.items())[0]
    return not acc.endswith('-8')


def get_num_tpu_devices(resources: Optional['resources_lib.Resources']) -> int:
    if resources is None or not is_tpu(resources):
        raise ValueError('resources must be a valid TPU resource.')
    acc, _ = list(resources.accelerators.items())[0]
    num_tpu_devices = int(int(acc.split('-')[2]) / 8)
    return num_tpu_devices
