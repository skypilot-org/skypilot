"""Constants used for Managed Spot."""

SPOT_CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP = 30
SPOT_CONTROLLER_NAME = 'sky-spot-controller'

SPOT_CONTROLLER_TEMPLATE = 'spot-controller.yaml.j2'
SPOT_CONTROLLER_YAML_PREFIX = '~/.sky/spot_controller'

SPOT_TASK_YAML_PREFIX = '~/.sky/spot_tasks'

SPOT_WORKDIR_BUCKET_NAME = 'sky-spot-workdir-{username}-{hash}'
SPOT_FILE_MOUNT_BUCKET_NAME = 'sky-spot-fm-{username}-{hash}'
