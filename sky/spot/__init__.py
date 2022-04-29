"""Modules for managed spot clusters."""
import pathlib

from sky.spot.controller import SpotController
from sky.spot.recovery_strategy import SPOT_STRATEGIES
from sky.spot.recovery_strategy import SPOT_DEFAULT_STRATEGY
from sky.spot.spot_utils import SpotCodeGen
from sky.spot.spot_utils import dump_job_table_cache
from sky.spot.spot_utils import load_job_table_cache

SPOT_CONTROLLER_AUTOSTOP_IDLE_MINUTES = 60
SPOT_CONTROLLER_NAME = 'sky-spot-controller'

SPOT_CONTROLLER_TEMPLATE = 'spot-controller.yaml.j2'
SPOT_CONTROLLER_YAML_PREFIX = '~/.sky/spot_controller'

SPOT_TASK_YAML_PATH = '~/.sky/spot_tasks'
pathlib.Path(SPOT_TASK_YAML_PATH).expanduser().parent.mkdir(parents=True,
                                                            exist_ok=True)

__all__ = [
    'SpotController',
    'SPOT_STRATEGIES',
    'SPOT_DEFAULT_STRATEGY',
    'SPOT_CONTROLLER_NAME',
    'SpotCodeGen',
    'dump_job_table_cache',
    'load_job_table_cache',
]
