"""Constants for SkyPilot."""

SKY_LOGS_DIRECTORY = '~/sky_logs'
SKY_REMOTE_WORKDIR = '~/sky_workdir'
SKY_REMOTE_RAY_PORT_FILE = '~/.sky/ray_port.json'
SKY_REMOTE_RAY_PORT = 6380
SKY_REMOTE_RAY_DASHBOARD_PORT = 8266
SKY_REMOTE_RAY_TEMPDIR = '/tmp/ray_skypilot'
SKY_REMOTE_RAY_VERSION = '2.4.0'

# TODO(mluo): Make explicit `sky launch -c <name> ''` optional.
UNINITIALIZED_ONPREM_CLUSTER_MESSAGE = (
    'Found uninitialized local cluster {cluster}. Run this '
    'command to initialize it locally: sky launch -c {cluster} \'\'')

JOB_ID_ENV_VAR = 'SKYPILOT_JOB_ID'
