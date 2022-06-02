"""Modules for managed spot clusters."""
import pathlib

from sky.spot.constants import (
    SPOT_CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP,
    SPOT_CONTROLLER_NAME,
    SPOT_CONTROLLER_TEMPLATE,
    SPOT_CONTROLLER_YAML_PREFIX,
    SPOT_TASK_YAML_PREFIX,
)
from sky.spot.controller import SpotController
from sky.spot.recovery_strategy import SPOT_STRATEGIES
from sky.spot.recovery_strategy import SPOT_DEFAULT_STRATEGY
from sky.spot.spot_utils import SpotCodeGen
from sky.spot.spot_utils import dump_job_table_cache
from sky.spot.spot_utils import load_job_table_cache

pathlib.Path(SPOT_TASK_YAML_PREFIX).expanduser().parent.mkdir(parents=True,
                                                              exist_ok=True)

__all__ = [
    'SpotController',
    'SPOT_STRATEGIES',
    'SPOT_DEFAULT_STRATEGY',
    # Constants
    'SPOT_CONTROLLER_NAME',
    'SPOT_CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP',
    'SPOT_CONTROLLER_TEMPLATE',
    'SPOT_CONTROLLER_YAML_PREFIX',
    'SPOT_TASK_YAML_PREFIX',
    # utils
    'SpotCodeGen',
    'dump_job_table_cache',
    'load_job_table_cache',
]
