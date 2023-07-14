"""Patch Ray modules.

All *.patch files in this directory are generated manually by

  >> diff original new >original.patch

This script applies patches by running the following

  >> patch original original.patch

To get original versions, go to the Ray branch with version:

  sky.constants.SKY_REMOTE_RAY_VERSION

Example workflow:

  >> wget https://raw.githubusercontent.com/ray-project/ray/releases/2.4.0/python/ray/autoscaler/_private/command_runner.py
  >> cp command_runner.py command_runner.py.1

  >> # Make some edits to command_runner.py.1...

  >> diff command_runner.py command_runner.py.1 >command_runner.py.patch

  >> # Inspect command_runner.py.patch.
  >> # Edit this file to include command_runner.py.patch.
"""
import os
import subprocess

import pkg_resources

from sky.skylet import constants


def _to_absolute(pwd_file):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), pwd_file)


def _run_patch(target_file,
               patch_file,
               version=constants.SKY_REMOTE_RAY_VERSION):
    """Applies a patch if it has not been applied already."""
    # .orig is the original file that is not patched.
    orig_file = os.path.abspath(f'{target_file}-v{version}.orig')
    script = f"""\
    which patch >/dev/null 2>&1 || sudo yum install -y patch || true
    which patch >/dev/null 2>&1 || (echo "`patch` is not found. Failed to setup ray." && exit 1)
    if [ ! -f {orig_file} ]; then
        echo Create backup file {orig_file}
        cp {target_file} {orig_file}
    fi
    # It is ok to patch again from the original file.
    patch {orig_file} -i {patch_file} -o {target_file}
    """
    subprocess.run(script, shell=True, check=True)


def patch() -> None:
    # Patch the buggy ray files. This should only be called
    # from an isolated python process, because once imported
    # the python module would persist in the memory.

    from ray._private import log_monitor
    _run_patch(log_monitor.__file__, _to_absolute('log_monitor.py.patch'))

    from ray._private import worker
    _run_patch(worker.__file__, _to_absolute('worker.py.patch'))

    from ray.dashboard.modules.job import cli
    _run_patch(cli.__file__, _to_absolute('cli.py.patch'))

    from ray.autoscaler._private import autoscaler
    _run_patch(autoscaler.__file__, _to_absolute('autoscaler.py.patch'))

    from ray.autoscaler._private import command_runner
    _run_patch(command_runner.__file__, _to_absolute('command_runner.py.patch'))

    from ray.autoscaler._private import resource_demand_scheduler
    _run_patch(resource_demand_scheduler.__file__,
               _to_absolute('resource_demand_scheduler.py.patch'))

    from ray.autoscaler._private import updater
    _run_patch(updater.__file__, _to_absolute('updater.py.patch'))

    from ray.dashboard.modules.job import job_head
    _run_patch(job_head.__file__, _to_absolute('job_head.py.patch'))
