"""The SkyPilot package."""
import os

# Keep this order to avoid cyclic imports
from sky import backends
from sky import benchmark
from sky import clouds
from sky.clouds.service_catalog import list_accelerators
from sky.dag import Dag
from sky.execution import launch, exec, spot_launch  # pylint: disable=redefined-builtin
from sky.resources import Resources
from sky.task import Task
from sky.optimizer import Optimizer, OptimizeTarget
from sky.data import Storage, StoreType
from sky.global_user_state import ClusterStatus
from sky.skylet.job_lib import JobStatus
from sky.core import (status, start, stop, down, autostop, queue, cancel,
                      spot_status, spot_cancel, storage_ls, storage_delete)

__root_dir__ = os.path.dirname(os.path.abspath(__file__))

# Aliases.
AWS = clouds.AWS
Azure = clouds.Azure
GCP = clouds.GCP
Local = clouds.Local
optimize = Optimizer.optimize

__all__ = [
    'AWS',
    'Azure',
    'GCP',
    'Local',
    'Optimizer',
    'OptimizeTarget',
    'backends',
    'benchmark',
    'list_accelerators',
    '__root_dir__',
    'Storage',
    'StoreType',
    'ClusterStatus',
    'JobStatus',
    # APIs
    'Dag',
    'Task',
    'Resources',
    'launch',
    'exec',
    'spot_launch',
    'status',
    'start',
    'stop',
    'down',
    'autostop',
    'queue',
    'cancel',
    'spot_status',
    'spot_cancel',
    'storage_ls',
    'storage_delete',
]
