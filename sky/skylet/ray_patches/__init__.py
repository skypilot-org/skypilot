"""Patch files"""
import os
import shutil


def patch() -> None:
    # Patch the buggy ray files. This should only be called
    # from an isolated python process, because once imported
    # the python module would persist in the memory.
    from ray import worker
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'worker.py')
    shutil.copyfile(path, worker.__file__)

    from ray.autoscaler._private import resource_demand_scheduler
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'resource_demand_scheduler.py')
    shutil.copyfile(path, resource_demand_scheduler.__file__)
