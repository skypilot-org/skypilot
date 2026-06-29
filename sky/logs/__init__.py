"""Sky logging agents."""
from typing import Optional

from sky import exceptions
from sky import skypilot_config
from sky.logs.agent import LoggingAgent
from sky.logs.aws import CloudwatchLoggingAgent
from sky.logs.gcp import GCPLoggingAgent
from sky.logs.reader import get_log_reader
from sky.logs.reader import LogReader
from sky.logs.reader import register_log_reader

__all__ = [
    'LoggingAgent',
    'CloudwatchLoggingAgent',
    'GCPLoggingAgent',
    'LogReader',
    'get_log_reader',
    'register_log_reader',
    'get_logging_agent',
]


def get_logging_agent() -> Optional[LoggingAgent]:
    store = skypilot_config.get_nested(('logs', 'store'), None)
    if store is None:
        return None
    if store == 'gcp':
        return GCPLoggingAgent(skypilot_config.get_nested(('logs', 'gcp'), {}))
    elif store == 'aws':
        return CloudwatchLoggingAgent(
            skypilot_config.get_nested(('logs', 'aws'), {}))
    raise exceptions.InvalidSkyPilotConfigError(
        f'Invalid logging store: {store}')
