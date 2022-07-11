"""Constants for usage collection."""

LOG_URL = 'http://54.218.118.250:9090/api/prom/push'  # pylint: disable=line-too-long

USAGE_MESSAGE_SCHEMA_VERSION = 6

PRIVACY_POLICY_PATH = '~/.sky/privacy_policy'

USAGE_POLICY_MESSAGE = (
    'Sky collects usage data to improve its services. '
    '`setup` and `run` commands are not collected to '
    'ensure privacy.\n'
    'Usage logging can be disabled by setting '
    'environment variable SKY_DISABLE_USAGE_COLLECTION=1 when invoking sky.')
