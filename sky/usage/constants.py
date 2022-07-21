"""Constants for usage collection."""

LOG_URL = 'http://54.68.242.202:9090/loki/api/v1/push'  # pylint: disable=line-too-long

USAGE_MESSAGE_SCHEMA_VERSION = 1

PRIVACY_POLICY_PATH = '~/.sky/privacy_policy'

USAGE_POLICY_MESSAGE = (
    'Sky collects usage data to improve its services. '
    '`setup` and `run` commands are not collected to '
    'ensure privacy.\n'
    'Usage logging can be disabled by setting '
    'environment variable SKY_DISABLE_USAGE_COLLECTION=1 when invoking sky.')

USAGE_MESSAGE_REDACT_KEYS = ['setup', 'run', 'envs']
USAGE_MESSAGE_REDACT_TYPES = {str, dict}

USAGE_USER_ENV = 'SKY_USAGE_USER_ID'
