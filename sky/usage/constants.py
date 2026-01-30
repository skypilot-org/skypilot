"""Constants for usage collection."""

LOG_URL = 'https://usage-v2.skypilot.co'

USAGE_MESSAGE_SCHEMA_VERSION = 1
PRIVACY_POLICY_PATH = '~/.sky/privacy_policy'

USAGE_POLICY_MESSAGE = (
    'SkyPilot collects usage data to improve its services. '
    '`setup` and `run` commands are not collected to '
    'ensure privacy.\n'
    'Usage logging can be disabled by setting the '
    'environment variable SKYPILOT_DISABLE_USAGE_COLLECTION=1.')

USAGE_MESSAGE_REDACT_KEYS = ['setup', 'run', 'envs', 'secrets']
USAGE_MESSAGE_REDACT_TYPES = {str, dict}

# Env var for the usage run id. This is used by the API server to associate
# the usage run id of a request from client to the actual functions invoked.
USAGE_RUN_ID_ENV_VAR = 'SKYPILOT_USAGE_RUN_ID'

# The file stores the usage run id on a remote cluster, so that the heartbeat
# on that remote cluster can be associated with the usage run id. This file is
# initialized when the cluster is firstly launched in:
# sky.provision.instance_setup.start_skylet_on_head_node
USAGE_RUN_ID_FILE = '~/.sky/usage_run_id'
