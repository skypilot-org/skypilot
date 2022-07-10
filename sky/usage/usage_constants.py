"""Constants for usage collection."""

LOG_URL = 'https://178762:eyJrIjoiN2VhYWQ3YWRkNzM0NDY0ZmE4YmRlNzRhYTk2ZGRhOWQ5ZjdkMGE0ZiIsIm4iOiJza3lwaWxvdC11c2VyLXN0YXRzLW1ldHJpY3MiLCJpZCI6NjE1MDQ2fQ=@logs-prod3.grafana.net/api/prom/push'  # pylint: disable=line-too-long

USAGE_MESSAGE_SCHEMA_VERSION = 6

PRIVACY_POLICY_PATH = '~/.sky/privacy_policy'

USAGE_POLICY_MESSAGE = (
    'Sky collects usage data to improve its services. '
    '`setup` and `run` commands are not collected to '
    'ensure privacy.\n'
    'Usage logging can be disabled by setting '
    'environment variable SKY_DISABLE_USAGE_COLLECTION=1 when invoking sky.')
