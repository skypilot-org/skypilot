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


def _run_patch(original_file, patch_file):
    """Applies a patch if it has not been applied already."""
    #  s: silent
    #  R: reverse (to test whether it has been applied)
    #  f: no confirmation in the normal case of when patch not applied
    # Adapted from https://unix.stackexchange.com/a/86872/9411
    # .orig is the original file that is not patched. We recover the original
    # file if it exists, before applying the patch to avoid the patching failure
    # when the file is already patched with an older version.
    orig_file = os.path.abspath(original_file + '.orig')
    script = f"""\
    if [ -f {orig_file} ]; then
        mv {orig_file} {original_file}
    fi
    if ! patch -sRf --dry-run {original_file} {patch_file} >/dev/null; then
        patch {original_file} {patch_file}
    else
        echo Patch {patch_file} skipped.
    fi
    """
    # /bin/bash is required to support True/False in the patch command.
    subprocess.run(script, shell=True, check=True, executable='/bin/bash')


def patch() -> None:
    # Patch the buggy ray files. This should only be called
    # from an isolated python process, because once imported
    # the python module would persist in the memory.
    from ray import worker
    _run_patch(worker.__file__, _to_absolute('worker.py.patch'))

    from ray.autoscaler._private import resource_demand_scheduler
    _run_patch(resource_demand_scheduler.__file__,
               _to_absolute('resource_demand_scheduler.py.patch'))

    from ray.autoscaler._private import autoscaler
    _run_patch(autoscaler.__file__, _to_absolute('autoscaler.py.patch'))
