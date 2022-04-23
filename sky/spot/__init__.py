"""Modules for managed spot clusters."""
import pathlib
from sky.spot.controller import SpotController
from sky.spot.recovery_strategy import SPOT_STRATEGIES

SPOT_CONTROLLER_NAME = 'sky-spot-controller'
SPOT_TASK_YAML_PATH = '~/.sky/spot_tasks'
pathlib.Path(SPOT_TASK_YAML_PATH).expanduser().parent.mkdir(parents=True,
                                                            exist_ok=True)

__all__ = [
    'SpotController',
    'SPOT_STRATEGIES',
    'SPOT_CONTROLLER_NAME',
]
