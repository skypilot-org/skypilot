"""Constants used for sky managed spot."""
import pathlib

SPOT_CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP = 30
SPOT_CONTROLLER_NAME = 'sky-spot-controller'

SPOT_CONTROLLER_TEMPLATE = 'spot-controller.yaml.j2'
SPOT_CONTROLLER_YAML_PREFIX = '~/.sky/spot_controller'

SPOT_TASK_YAML_PREFIX = '~/.sky/spot_tasks'
pathlib.Path(SPOT_TASK_YAML_PREFIX).expanduser().parent.mkdir(parents=True,
                                                              exist_ok=True)
