"""The SkyPilot package."""
import os

# Keep this order to avoid cyclic imports
from sky import backends
from sky import benchmark
from sky import clouds
from sky.clouds.service_catalog import list_accelerators
from sky.dag import Dag, DagContext
from sky.execution import launch, exec  # pylint: disable=redefined-builtin
from sky.resources import Resources
from sky.task import Task
from sky.optimizer import Optimizer, OptimizeTarget
from sky.data import Storage, StoreType

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
    'Dag',
    'DagContext',
    'Local',
    'Optimizer',
    'OptimizeTarget',
    'Resources',
    'Task',
    'backends',
    'benchmark',
    'launch',
    'exec',
    'list_accelerators',
    '__root_dir__',
    'Storage',
    'StoreType',
]
