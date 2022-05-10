"""Patch Ray modules.

All *.patch files in this directory are generated manually by

  >> diff original new >original.patch

They should be applied using

  >> patch original original.patch

Ray version to check out the original files:
  sky.backends.backend_utils.SKY_REMOTE_RAY_VERSION

"""
import os
import shutil
import subprocess


def _to_absolute_path(pwd_file):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), pwd_file)

def patch() -> None:
    # Patch the buggy ray files. This should only be called
    # from an isolated python process, because once imported
    # the python module would persist in the memory.
    from ray import worker
    path = _to_absolute_path('worker.py')
    shutil.copyfile(path, worker.__file__)

    from ray.autoscaler._private import resource_demand_scheduler
    subprocess.run([
        'patch',
        resource_demand_scheduler.__file__,
        _to_absolute_path('resource_demand_scheduler.py.patch'),
    ],
                   check=True)

    # path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
    #                     'resource_demand_scheduler.py')
    # shutil.copyfile(path, resource_demand_scheduler.__file__)
