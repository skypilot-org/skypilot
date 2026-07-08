"""Sky logging agents."""
from typing import Callable, Optional

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
    'register_logging_agent_provider',
]

# An optional programmatic override for the logging agent, mirroring the
# read-side ``LogReader`` registry (``register_log_reader``). When registered,
# it is consulted before the ``logs.store`` config selection, so a caller can
# supply a logging agent whose destination/credentials are resolved at runtime
# rather than from static config. Returning ``None`` falls back to the
# config-based selection, so the override never has to reimplement it.
LoggingAgentProvider = Callable[[], Optional[LoggingAgent]]
_logging_agent_provider: Optional[LoggingAgentProvider] = None


def register_logging_agent_provider(
        provider: Optional[LoggingAgentProvider]) -> None:
    """Registers the process-global logging agent provider (None to clear)."""
    global _logging_agent_provider
    _logging_agent_provider = provider


def get_logging_agent() -> Optional[LoggingAgent]:
    # Capture into a local so a concurrent register_logging_agent_provider(None)
    # cannot clear the global between the check and the call.
    provider = _logging_agent_provider
    if provider is not None:
        agent = provider()
        if agent is not None:
            return agent
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
