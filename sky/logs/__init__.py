"""Sky logging agents."""
import inspect
from typing import Callable, Optional

from sky import exceptions
from sky import skypilot_config
from sky.logs.agent import LoggingAgent
from sky.logs.aws import CloudwatchLoggingAgent
from sky.logs.gcp import GCPLoggingAgent
from sky.logs.otlp import OtlpLoggingAgent
from sky.logs.reader import get_log_reader
from sky.logs.reader import LogReader
from sky.logs.reader import register_log_reader

__all__ = [
    'LoggingAgent',
    'CloudwatchLoggingAgent',
    'GCPLoggingAgent',
    'OtlpLoggingAgent',
    'LogReader',
    'get_log_reader',
    'register_log_reader',
    'get_logging_agent',
    'is_logging_agent_configured',
    'register_logging_agent_provider',
]

# An optional programmatic override for the logging agent, mirroring the
# read-side ``LogReader`` registry (``register_log_reader``). When registered,
# it is consulted before the ``logs.store`` config selection. Returning
# ``None`` falls back to the config-based selection, so the override never has
# to reimplement it.
#
# A provider may accept the keyword arguments ``cloud`` and ``region`` (for
# Kubernetes and SSH the region is the context) to route a cluster's logs by
# where it runs. A provider that takes no arguments is called without them and
# resolves one agent for every cluster.
LoggingAgentProvider = Callable[..., Optional[LoggingAgent]]
_logging_agent_provider: Optional[LoggingAgentProvider] = None
_provider_takes_placement = False


def _accepts_placement(provider: LoggingAgentProvider) -> bool:
    try:
        inspect.signature(provider).bind(cloud=None, region=None)
    except (TypeError, ValueError):
        return False
    return True


def register_logging_agent_provider(
        provider: Optional[LoggingAgentProvider]) -> None:
    """Registers the process-global logging agent provider (None to clear)."""
    global _logging_agent_provider, _provider_takes_placement
    _provider_takes_placement = (provider is not None and
                                 _accepts_placement(provider))
    _logging_agent_provider = provider


def get_logging_agent(cloud: Optional[str] = None,
                      region: Optional[str] = None) -> Optional[LoggingAgent]:
    """Returns the logging agent for a cluster placed at (cloud, region).

    Args:
        cloud: Lower-case cloud name of the cluster, or None when the caller
            has no cluster in scope.
        region: The cluster's region; the context for Kubernetes and SSH.
    """
    # Capture into locals so a concurrent register_logging_agent_provider(None)
    # cannot clear the global between the check and the call.
    provider = _logging_agent_provider
    takes_placement = _provider_takes_placement
    if provider is not None:
        if takes_placement:
            agent = provider(cloud=cloud, region=region)
        else:
            agent = provider()
        if agent is not None:
            return agent
    store = skypilot_config.get_nested(('logs', 'store'), None)
    if store is None:
        return None
    # The config schema accepts the store name case-insensitively.
    store = store.lower()
    if store == 'gcp':
        return GCPLoggingAgent(skypilot_config.get_nested(('logs', 'gcp'), {}))
    elif store == 'aws':
        return CloudwatchLoggingAgent(
            skypilot_config.get_nested(('logs', 'aws'), {}))
    elif store == 'otlp':
        return OtlpLoggingAgent(skypilot_config.get_nested(('logs', 'otlp'),
                                                           {}))
    raise exceptions.InvalidSkyPilotConfigError(
        f'Invalid logging store: {store}')


def is_logging_agent_configured(cloud: Optional[str] = None,
                                region: Optional[str] = None) -> bool:
    """Returns whether a logging agent is configured for (cloud, region).

    When True, job logs are forwarded to an external store, so callers can
    stream them on demand and fall back to the registered ``LogReader`` instead
    of persisting a local copy.
    """
    return get_logging_agent(cloud=cloud, region=region) is not None
