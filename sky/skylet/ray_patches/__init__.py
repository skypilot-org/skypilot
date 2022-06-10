"""Patch Ray modules.

All *.patch files in this directory are generated manually by

  >> diff original new >original.patch

This script applies patches by running the following

  >> patch original original.patch

To get original versions, go to the Ray branch with version:

  sky.backends.backend_utils.SKY_REMOTE_RAY_VERSION

Example:
- https://raw.githubusercontent.com/ray-project/ray/releases/1.10.0/python/ray/worker.py
"""
import os
import subprocess


def _to_absolute(pwd_file):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), pwd_file)


def _run_patch(target_file, patch_file):
    """Applies a patch if it has not been applied already."""
    # .orig is the original file that is not patched.
    orig_file = os.path.abspath(target_file + '.orig')
    script = f"""\
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
    import ray
    if ray.__version__ != '1.10.0':
        raise Exception(f'Cannot apply Ray patches to {ray.__version__} other than 1.10.0.')

    from ray import worker
    _run_patch(worker.__file__, _to_absolute('worker.py.patch'))

    from ray.autoscaler._private import resource_demand_scheduler
    _run_patch(resource_demand_scheduler.__file__,
               _to_absolute('resource_demand_scheduler.py.patch'))

    from ray.autoscaler._private import autoscaler
    _run_patch(autoscaler.__file__, _to_absolute('autoscaler.py.patch'))

    from ray.autoscaler._private import commands
    _run_patch(commands.__file__, _to_absolute('commands.py.patch'))
