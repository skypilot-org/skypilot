"""Constants for SkyPilot."""

SKY_LOGS_DIRECTORY = '~/sky_logs'
SKY_REMOTE_WORKDIR = '~/sky_workdir'
SKY_REMOTE_RAY_VERSION = '2.0.1'

# TODO(mluo): Make explicit `sky launch -c <name> ''` optional.
UNINITIALIZED_ONPREM_CLUSTER_MESSAGE = (
    'Found uninitialized local cluster {cluster}. Run this '
    'command to initialize it locally: sky launch -c {cluster} \'\'')

JOB_ID_ENV_VAR = 'SKYPILOT_JOB_ID'
