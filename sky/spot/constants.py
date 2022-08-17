"""Constants used for Managed Spot."""

SPOT_CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP = 30
SPOT_CONTROLLER_NAME = 'sky-spot-controller'

SPOT_CONTROLLER_TEMPLATE = 'spot-controller.yaml.j2'
SPOT_CONTROLLER_YAML_PREFIX = '~/.sky/spot_controller'

SPOT_TASK_YAML_PREFIX = '~/.sky/spot_tasks'

SPOT_WORKDIR_BUCKET_NAME = 'skypilot-spot-workdir-{username}-{id}'
SPOT_FM_BUCKET_NAME = 'skypilot-spot-filemounts-folder-{username}-{id}'
SPOT_FM_FILE_ONLY_BUCKET_NAME = 'sky-spot-filemounts-files-{username}-{id}'
SPOT_FM_LOCAL_TMP_DIR = 'sky-spot-filemounts-files-{id}'
SPOT_FM_REMOTE_TMP_DIR = '/tmp/sky-spot-filemounts-files'
