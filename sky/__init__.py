"""The SkyPilot package."""
import os

# Replaced with the current commit when building the wheels.
__commit__ = '{{SKYPILOT_COMMIT_SHA}}'
__version__ = '1.0.0-dev0'
__root_dir__ = os.path.dirname(os.path.abspath(__file__))

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
from sky.data import Storage, StorageMode, StoreType
from sky.global_user_state import ClusterStatus
from sky.skylet.job_lib import JobStatus
from sky.core import (status, start, stop, down, autostop, queue, cancel,
                      tail_logs, download_logs, job_status, spot_queue,
                      spot_status, spot_cancel, storage_ls, storage_delete)

# Aliases.
AWS = clouds.AWS
Azure = clouds.Azure
GCP = clouds.GCP
Local = clouds.Local
optimize = Optimizer.optimize

__all__ = [
    '__version__',
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
    'StorageMode',
    'StoreType',
    'ClusterStatus',
    'JobStatus',
    # APIs
    'Dag',
    'Task',
    'Resources',
    # execution APIs
    'launch',
    'exec',
    'spot_launch',
    # core APIs
    'status',
    'start',
    'stop',
    'down',
    'autostop',
    # core APIs Job Management
    'queue',
    'cancel',
    'tail_logs',
    'download_logs',
    'job_status',
    # core APIs Spot Job Management
    'spot_queue',
    'spot_status',  # Deprecated (alias for spot_queue)
    'spot_cancel',
    # core APIs Storage Management
    'storage_ls',
    'storage_delete',
]
