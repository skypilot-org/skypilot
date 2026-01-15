"""External node info source interface for plugins.

This module provides an extension point that allows plugins to provide
cached Kubernetes node information. By default, no-op implementations
are used. Plugins can register their own implementations to provide
cached node info (e.g., from a node-info-service sidecar).

Example usage in a plugin:
    from sky.utils.plugin_extensions import NodeInfoSource

    # Register custom node info provider
    NodeInfoSource.register(my_node_info_provider)

Example usage in core SkyPilot:
    from sky.utils.plugin_extensions import NodeInfoSource

    # Get cached node info (returns None if not registered or unavailable)
    node_info = NodeInfoSource.get(context='my-k8s-context')
"""
from typing import Callable, Optional

from sky import models
from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# Type alias for the node info provider function
# Function signature: (context: str) -> Optional[KubernetesNodesInfo]
NodeInfoProviderFunc = Callable[[str], Optional[models.KubernetesNodesInfo]]


class NodeInfoSource:
    """Singleton class for external Kubernetes node info source.

    This class provides an extension point for plugins to register their own
    node info providers (e.g., a node-info-service that caches Kubernetes
    node information). By default, no provider is registered and get()
    returns None.

    Plugins can register their provider during their install() phase,
    and core SkyPilot code can use the get() method to attempt to retrieve
    cached node info before falling back to direct Kubernetes API calls.
    """

    _provider_func: Optional[NodeInfoProviderFunc] = None

    @classmethod
    def register(cls, provider: NodeInfoProviderFunc) -> None:
        """Register a node info provider function.

        This allows plugins to provide cached Kubernetes node information.
        Only one provider can be registered at a time.

        Args:
            provider: Function to get node info for a context.
                Signature: (context: str) -> Optional[KubernetesNodesInfo]
                Returns KubernetesNodesInfo if available, None otherwise.
        """
        cls._provider_func = provider
        logger.debug('Registered external node info provider')

    @classmethod
    def is_registered(cls) -> bool:
        """Check if an external node info provider is registered."""
        return cls._provider_func is not None

    @classmethod
    def get(cls, context: str) -> Optional[models.KubernetesNodesInfo]:
        """Get node info from the registered provider.

        Args:
            context: Kubernetes context name to get node info for.

        Returns:
            KubernetesNodesInfo if provider returns data, None otherwise.
            Returns None if no provider is registered or if the provider
            fails or returns None (e.g., context not available).
        """
        if cls._provider_func is None:
            return None
        try:
            # pylint: disable=not-callable
            return cls._provider_func(context)
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'External node info provider failed: {e}')
            return None
