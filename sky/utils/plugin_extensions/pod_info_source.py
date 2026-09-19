"""External pod info source interface for plugins.

This module provides an extension point that allows plugins to provide
cached Kubernetes pod information. By default, no-op implementations
are used. Plugins can register their own implementations to serve pod
info from an external cache (e.g. an in-cluster informer cache) instead
of paying a direct Kubernetes API round-trip on every read.

Example usage in a plugin:
    from sky.utils.plugin_extensions import PodInfoSource

    # Register custom pod info provider
    PodInfoSource.register(my_pod_info_provider)

Example usage in core SkyPilot:
    from sky.utils.plugin_extensions import PodInfoSource

    # Get cached pod info (returns None if not registered or unavailable)
    pods = PodInfoSource.get(context='my-k8s-context')

    # Or scoped to a specific namespace / label selector — useful for
    # per-cluster queries like ``query_instances``:
    pods = PodInfoSource.get(
        context='my-k8s-context',
        namespace='default',
        labels={'skypilot-cluster-name': 'sky-cmd-1'},
    )
"""
from typing import Any, Callable, Dict, List, Optional

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# Type alias for the pod info provider function.
#
# Function signature: (context, namespace, labels) -> Optional[List[Any]]
# - context: K8s context name (required).
# - namespace: optional namespace filter; ``None`` means all namespaces.
# - labels: optional label filter. Keys with ``None`` values mean
#   "label exists" (existence check); keys with string values mean
#   "label exists and equals this value" (exact match).
# Returns a list of V1Pod-compatible objects if available, ``None`` otherwise.
# Returning ``None`` means "no cached data, fall through to direct API".
# Returning ``[]`` means "checked cache, no SkyPilot pods found" (valid result).
PodInfoProviderFunc = Callable[
    [str, Optional[str], Optional[Dict[str,
                                       Optional[str]]]], Optional[List[Any]],]


class PodInfoSource:
    """Singleton class for external Kubernetes pod info source.

    This class provides an extension point for plugins to register their own
    pod info providers (e.g. a process backed by an in-cluster informer
    cache). By default, no provider is registered and ``get()`` returns
    ``None``.

    Plugins can register their provider during their install() phase,
    and core SkyPilot code can use the get() method to attempt to retrieve
    cached pod info before falling back to direct Kubernetes API calls.
    """

    _provider_func: Optional[PodInfoProviderFunc] = None

    @classmethod
    def register(cls, provider: PodInfoProviderFunc) -> None:
        """Register a pod info provider function.

        This allows plugins to provide cached Kubernetes pod information.
        Only one provider can be registered at a time.

        Args:
            provider: Function to get pod info for a context, optionally
                filtered by namespace and labels.
                Signature:
                  ``(context, namespace, labels) -> Optional[List[Any]]``
                Returns a list of V1Pod-compatible objects if available,
                ``None`` otherwise.
        """
        cls._provider_func = provider
        logger.debug('Registered external pod info provider')

    @classmethod
    def is_registered(cls) -> bool:
        """Check if an external pod info provider is registered."""
        return cls._provider_func is not None

    @classmethod
    def get(
        cls,
        context: str,
        namespace: Optional[str] = None,
        labels: Optional[Dict[str, Optional[str]]] = None,
    ) -> Optional[List[Any]]:
        """Get pod info from the registered provider.

        Args:
            context: Kubernetes context name to get pod info for.
            namespace: Optional namespace filter. ``None`` means
                "all namespaces" (matching the cluster-wide
                ``list_pod_for_all_namespaces`` semantics).
            labels: Optional label filter. Each entry restricts the result
                set: a ``None`` value means "label exists" (existence
                check); a string value means "label exists and equals this
                value" (exact match). ``None`` means "no label filter".

        Returns:
            List of V1Pod-compatible objects if provider returns data,
            None otherwise. Returns None if no provider is registered
            or if the provider fails or returns None (e.g., context
            not available).
        """
        if cls._provider_func is None:
            return None
        try:
            # pylint: disable=not-callable
            return cls._provider_func(context, namespace, labels)
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'External pod info provider failed: {e}')
            return None
