"""Sky logging agents."""
from typing import Optional

from sky import exceptions
from sky import skypilot_config
from sky.logs.agent import LoggingAgent
from sky.logs.gcp import GCPLoggingAgent


def get_logging_agent() -> Optional[LoggingAgent]:
    store = skypilot_config.get_nested(('logs', 'store'), None)
    if store is None:
        return None
    if store == 'gcp':
        return GCPLoggingAgent(skypilot_config.get_nested(('logs', 'gcp'), {}))
    raise exceptions.InvalidSkyPilotConfigError(
        f'Invalid logging store: {store}')
