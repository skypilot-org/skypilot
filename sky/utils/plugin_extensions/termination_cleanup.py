"""Extension point for cleanup callbacks on cluster termination.

This module provides an extension point that allows plugins to clean up
external resources they associate with a cluster (e.g. objects created
by an external scheduler or admission system for the cluster's pod
group) when the cluster is terminated. By default, no callbacks are
registered and termination behaves as before.

Example usage in a plugin:
    from sky.utils.plugin_extensions import TerminationCleanup

    def my_cleanup(cluster_name_on_cloud: str,
                   provider_config: Dict[str, Any],
                   worker_only: bool) -> None:
        ...

    TerminationCleanup.register(my_cleanup)

Example usage in core SkyPilot:
    from sky.utils import plugin_extensions

    # After the cluster's instances have been terminated:
    plugin_extensions.TerminationCleanup.run(cluster_name_on_cloud,
                                             provider_config,
                                             worker_only=worker_only)
"""
from typing import Any, Callable, Dict, List

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# Type alias for termination cleanup callbacks.
# Signature: (cluster_name_on_cloud, provider_config, worker_only) -> None
# worker_only is True when only the worker nodes were terminated (the
# head node stays up); callbacks decide for themselves whether a partial
# termination warrants any cleanup.
TerminationCleanupFunc = Callable[[str, Dict[str, Any], bool], None]


class TerminationCleanup:
    """Singleton registry of post-termination cleanup callbacks.

    Plugins can register callbacks during their install() phase. Core
    SkyPilot invokes run() after a cluster's instances have been
    terminated. Callbacks are best-effort: an exception from a callback
    is logged and never fails the termination, and remaining callbacks
    still run.
    """

    _callbacks: List[TerminationCleanupFunc] = []

    @classmethod
    def register(cls, callback: TerminationCleanupFunc) -> None:
        """Register a cleanup callback to run on cluster termination."""
        cls._callbacks.append(callback)
        logger.debug('Registered termination cleanup callback '
                     f'{getattr(callback, "__name__", callback)!r}')

    @classmethod
    def is_registered(cls) -> bool:
        """Check if any cleanup callback is registered."""
        return bool(cls._callbacks)

    @classmethod
    def run(cls, cluster_name_on_cloud: str, provider_config: Dict[str, Any],
            worker_only: bool) -> None:
        """Run all registered cleanup callbacks, best-effort."""
        for callback in cls._callbacks:
            try:
                callback(cluster_name_on_cloud, provider_config, worker_only)
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(
                    'Termination cleanup callback '
                    f'{getattr(callback, "__name__", callback)!r} failed for '
                    f'cluster {cluster_name_on_cloud!r}: {e}')
