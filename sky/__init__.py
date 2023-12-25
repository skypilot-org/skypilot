"""The SkyPilot package."""
import os
import subprocess

# Replaced with the current commit when building the wheels.
_SKYPILOT_COMMIT_SHA = '{{SKYPILOT_COMMIT_SHA}}'


def get_git_commit():
    if 'SKYPILOT_COMMIT_SHA' not in _SKYPILOT_COMMIT_SHA:
        # This is a release build, so we don't need to get the commit hash from
        # git, as it's already been set.
        return _SKYPILOT_COMMIT_SHA

    # This is a development build (pip install -e .), so we need to get the
    # commit hash from git.
    try:
        cwd = os.path.dirname(__file__)
        commit_hash = subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'],
            cwd=cwd,
            universal_newlines=True,
            stderr=subprocess.DEVNULL).strip()
        changes = subprocess.check_output(['git', 'status', '--porcelain'],
                                          cwd=cwd,
                                          universal_newlines=True,
                                          stderr=subprocess.DEVNULL).strip()
        if changes:
            commit_hash += '-dirty'
        return commit_hash
    except Exception:  # pylint: disable=broad-except
        return _SKYPILOT_COMMIT_SHA


__commit__ = get_git_commit()
__version__ = '1.0.0-dev0'
__root_dir__ = os.path.dirname(os.path.abspath(__file__))

# Keep this order to avoid cyclic imports
# pylint: disable=wrong-import-position
from sky import backends
from sky import benchmark
from sky import clouds
from sky.clouds.service_catalog import list_accelerators
from sky.core import autostop
from sky.core import cancel
from sky.core import cost_report
from sky.core import down
from sky.core import download_logs
from sky.core import job_status
from sky.core import queue
from sky.core import spot_cancel
from sky.core import spot_queue
from sky.core import spot_status
from sky.core import start
from sky.core import status
from sky.core import stop
from sky.core import storage_delete
from sky.core import storage_ls
from sky.core import tail_logs
from sky.dag import Dag
from sky.data import Storage
from sky.data import StorageMode
from sky.data import StoreType
from sky.execution import exec  # pylint: disable=redefined-builtin
from sky.execution import launch
from sky.execution import spot_launch
from sky.optimizer import Optimizer
from sky.optimizer import OptimizeTarget
from sky.resources import Resources
from sky.skylet.job_lib import JobStatus
from sky.status_lib import ClusterStatus
from sky.task import Task

# Aliases.
IBM = clouds.IBM
AWS = clouds.AWS
Azure = clouds.Azure
GCP = clouds.GCP
Lambda = clouds.Lambda
SCP = clouds.SCP
Local = clouds.Local
Kubernetes = clouds.Kubernetes
OCI = clouds.OCI
optimize = Optimizer.optimize

__all__ = [
    '__version__',
    'AWS',
    'Azure',
    'GCP',
    'IBM',
    'Kubernetes',
    'Lambda',
    'Local',
    'OCI',
    'SCP',
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
    'cost_report',
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
