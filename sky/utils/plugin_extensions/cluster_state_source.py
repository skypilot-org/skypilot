"""External cluster state source interface for plugins.

This module provides an extension point for serving Kubernetes object reads
from a cache, such as a watch-based informer cache. By default no source is
registered and every read goes to the Kubernetes API server.

Example usage in a plugin:
    from sky.utils.plugin_extensions import ClusterStateSource

    ClusterStateSource.register(my_provider)

Core SkyPilot code reads through ``sky.provision.kubernetes.reads``, which
asks the registered source first for reads that accept cached objects.
"""
import dataclasses
from typing import Any, Callable, Dict, List, Optional, Union

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# Reasons a source gives for not serving a read.
CONTEXT_NOT_REGISTERED = 'context_not_registered'
RESOURCE_NOT_CACHED = 'resource_not_cached'
UNSUPPORTED_SELECTOR = 'unsupported_selector'
WATCH_FAILING = 'watch_failing'
ERROR = 'error'


@dataclasses.dataclass(frozen=True)
class ObjectQuery:
    """A read of the objects of one resource type in one Kubernetes context.

    Attributes:
        context: Kubernetes context name.
        group: API group; '' for the core group.
        version: API version, e.g. 'v1'.
        resource: Plural resource name, e.g. 'pods'.
        namespace: Namespace to read; None for every namespace, or for a
            cluster-scoped resource.
        name: Name of the one object to read; None for every object.
        label_selector: Label selector in Kubernetes syntax, or None.
        field_selector: Field selector in Kubernetes syntax, or None.
        max_staleness_seconds: How long the cache's watch of this resource
            may have been failing; 0 accepts only a cache whose watch is
            healthy.
    """
    context: str
    group: str
    version: str
    resource: str
    namespace: Optional[str]
    name: Optional[str]
    label_selector: Optional[str]
    field_selector: Optional[str]
    max_staleness_seconds: float


@dataclasses.dataclass
class CachedObjects:
    """Objects a source serves for a query.

    Attributes:
        items: The selected objects in Kubernetes API JSON, as the API
            server's LIST returns them and in the same order.
        resource_version: The resourceVersion of the cache.
        staleness_seconds: How long the cache's watch of this resource has
            been failing; 0 while it is healthy.
    """
    items: List[Dict[str, Any]]
    resource_version: str
    staleness_seconds: float


@dataclasses.dataclass
class Refusal:
    """Why a source did not serve a query.

    Attributes:
        reason: One of the reasons defined in this module.
        message: Details for logs.
    """
    reason: str
    message: str = ''


# Function signature: (query: ObjectQuery) -> CachedObjects | Refusal
ClusterStateProviderFunc = Callable[[ObjectQuery], Union[CachedObjects,
                                                         Refusal]]


class ClusterStateSource:
    """Singleton class for an external source of Kubernetes objects.

    Plugins can register their provider during their install() phase.
    A provider serves a query only when its cache of that resource type in
    that context satisfies the query's max_staleness_seconds, and otherwise
    refuses it with a reason.
    """

    _provider_func: Optional[ClusterStateProviderFunc] = None

    @classmethod
    def register(cls, provider: ClusterStateProviderFunc) -> None:
        """Register a cluster state provider function.

        Only one provider can be registered at a time.
        """
        cls._provider_func = provider
        logger.debug('Registered external cluster state provider')

    @classmethod
    def is_registered(cls) -> bool:
        """Check if an external cluster state provider is registered."""
        return cls._provider_func is not None

    @classmethod
    def get(cls, query: ObjectQuery) -> Optional[Union[CachedObjects, Refusal]]:
        """Ask the registered provider to serve a query.

        Returns:
            None if no provider is registered; otherwise what the provider
            returns, or a Refusal with reason ERROR if it raises.
        """
        if cls._provider_func is None:
            return None
        try:
            # pylint: disable=not-callable
            return cls._provider_func(query)
        except Exception as e:  # pylint: disable=broad-except
            return Refusal(ERROR, f'{type(e).__name__}: {e}')
