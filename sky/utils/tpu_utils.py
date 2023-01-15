"""Utility functions for TPUs."""
import json
import os
from typing import Optional

from packaging import version

from sky import resources as resources_lib
from sky.skylet import log_lib
from sky.utils import ux_utils


def is_tpu(resources: Optional[resources_lib.Resources]) -> bool:
    if resources is None or resources.accelerators is None:
        return False
    acc, _ = list(resources.accelerators.items())[0]
    return acc.startswith('tpu')


def is_tpu_vm(resources: Optional[resources_lib.Resources]) -> bool:
    if resources is None or resources.accelerator_args is None:
        return False
    return resources.accelerator_args.get('tpu_vm', False)


def is_tpu_vm_pod(resources: Optional[resources_lib.Resources]) -> bool:
    if resources is None or not is_tpu_vm(resources):
        return False
    acc, _ = list(resources.accelerators.items())[0]
    return acc not in ['tpu-v2-8', 'tpu-v3-8']


def get_num_tpu_devices(
        resources: Optional[resources_lib.Resources]) -> Optional[int]:
    if resources is None or not is_tpu(resources):
        return None
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


def terminate_tpu_vm_cluster_cmd(cluster_name: str,
                                 zone: str,
                                 log_path: str = os.devnull) -> str:
    check_gcp_cli_include_tpu_vm()
    query_cmd = (f'gcloud compute tpus tpu-vm list --filter='
                 f'"(labels.ray-cluster-name={cluster_name})" '
                 f'--zone={zone} --format="value(name)"')
    returncode, stdout, stderr = log_lib.run_with_log(query_cmd,
                                                      log_path,
                                                      shell=True,
                                                      stream_logs=False,
                                                      require_outputs=True)

    # Skip the termination command, if the TPU ID
    # query command fails.
    if returncode != 0:
        terminate_cmd = (f'echo "cmd: {query_cmd}" && '
                         f'echo "{stdout}" && '
                         f'echo "{stderr}" >&2 && '
                         f'exit {returncode}')
    else:
        # Needs to create a list as GCP does not allow deleting
        # multiple TPU VMs at once.
        tpu_terminate_cmds = []
        for tpu_id in stdout.splitlines():
            tpu_terminate_cmds.append('gcloud compute tpus tpu-vm delete '
                                      f'--zone={zone} --quiet {tpu_id}')
        terminate_cmd = ' && '.join(tpu_terminate_cmds)
    return terminate_cmd
