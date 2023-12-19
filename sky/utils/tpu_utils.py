"""Utility functions for TPUs."""
import json
import os
import typing
from typing import Optional

from packaging import version

from sky.skylet import log_lib
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib


def is_tpu(resources: Optional['resources_lib.Resources']) -> bool:
    if resources is None or resources.accelerators is None:
        return False
    acc, _ = list(resources.accelerators.items())[0]
    return acc.startswith('tpu')


def is_tpu_vm(resources: Optional['resources_lib.Resources']) -> bool:
    if resources is None or resources.accelerator_args is None:
        return False
    return resources.accelerator_args.get('tpu_vm', False)


def is_tpu_vm_pod(resources: Optional['resources_lib.Resources']) -> bool:
    if resources is None or not is_tpu_vm(resources):
        return False
    acc, _ = list(resources.accelerators.items())[0]
    return acc not in ['tpu-v2-8', 'tpu-v3-8', 'tpu-v4-8']


def get_num_tpu_devices(resources: Optional['resources_lib.Resources']) -> int:
    if resources is None or not is_tpu(resources):
        raise ValueError('resources must be a valid TPU resource.')
    acc, _ = list(resources.accelerators.items())[0]
    num_tpu_devices = int(int(acc.split('-')[2]) / 8)
    return num_tpu_devices


def check_gcp_cli_include_tpu_vm() -> None:
    # TPU VM API available with gcloud version >= 382.0.0
    version_cmd = 'gcloud version --format=json'
    rcode, stdout, stderr = log_lib.run_with_log(version_cmd,
                                                 os.devnull,
                                                 shell=True,
                                                 stream_logs=False,
                                                 require_outputs=True)

    if rcode != 0:
        failure_massage = ('Failed to run "gcloud version".\n'
                           '**** STDOUT ****\n'
                           '{stdout}\n'
                           '**** STDERR ****\n'
                           '{stderr}')
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                failure_massage.format(stdout=stdout, stderr=stderr))

    sdk_ver = json.loads(stdout).get('Google Cloud SDK', None)

    if sdk_ver is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError('Failed to get Google Cloud SDK version from'
                               f' "gcloud version": {stdout}')
    else:
        major_ver = version.parse(sdk_ver).major
        if major_ver < 382:
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    'Google Cloud SDK version must be >= 382.0.0 to use'
                    ' TPU VM APIs, check "gcloud version" for details.')
