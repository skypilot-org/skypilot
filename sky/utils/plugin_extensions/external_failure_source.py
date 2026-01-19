"""External failure source interface for plugins.

This module provides an extension point that allows plugins to provide
cluster failure tracking functionality. By default, no-op implementations
are used. Plugins can register their own implementations to provide actual
failure tracking.

Example usage in a plugin:
    from sky.utils.plugin_extensions import ExternalFailureSource

    # Register custom failure source
    ExternalFailureSource.register(
        get_failures=my_get_cluster_failures,
        clear_failures=my_clear_cluster_failures,
    )

Example usage in core SkyPilot:
    from sky.utils.plugin_extensions import ExternalFailureSource

    # Get failures for a cluster
    failures = ExternalFailureSource.get(cluster_hash='abc123')

    # Clear failures for a cluster
    cleared = ExternalFailureSource.clear(cluster_name='my-cluster')
"""
import dataclasses
from typing import Any, Dict, List, Optional, Protocol

from sky import sky_logging

logger = sky_logging.init_logger(__name__)


@dataclasses.dataclass
class ExternalClusterFailure:
    """Represents a single cluster failure from an external source.

    Attributes:
        code: Machine-readable failure code (e.g. 'GPU_HARDWARE_FAILURE_XID_79')
        reason: Human-readable description of the failure.
    """
    code: str
    reason: str

    @classmethod
    def from_failure_list(
            cls, failures: List[Dict[str,
                                     Any]]) -> List['ExternalClusterFailure']:
        """Create a list of ExternalClusterFailure from failure dicts.

        Args:
            failures: List of dicts with 'failure_mode' and 'failure_reason'
                keys (as returned by ExternalFailureSource.get()).

        Returns:
            List of ExternalClusterFailure objects, one per failure.
        """
        return [
            cls(code=f['failure_mode'], reason=f['failure_reason'])
            for f in failures
        ]


# Protocol definitions for the failure source functions
class GetClusterFailuresFunc(Protocol):
    """Protocol for get_cluster_failures function."""

    def __call__(self,
                 cluster_hash: Optional[str] = None,
                 cluster_name: Optional[str] = None) -> List[Dict[str, Any]]:
        ...


class ClearClusterFailuresFunc(Protocol):
    """Protocol for clear_cluster_failures function."""

    def __call__(self,
                 cluster_hash: Optional[str] = None,
                 cluster_name: Optional[str] = None) -> List[Dict[str, Any]]:
        ...


class ExternalFailureSource:
    """Singleton class for external cluster failure source.

    This class provides an extension point for plugins to register their own
    cluster failure tracking implementations. By default, no-op implementations
    are used that return empty lists.

    Plugins can register their implementations during their install() phase,
    and core SkyPilot code can use the get() and clear() methods to interact
    with cluster failures without knowing which plugin (if any) is providing
    the implementation.
    """

    _get_func: Optional[GetClusterFailuresFunc] = None
    _clear_func: Optional[ClearClusterFailuresFunc] = None

    @classmethod
    def register(cls, get_failures: GetClusterFailuresFunc,
                 clear_failures: ClearClusterFailuresFunc) -> None:
        """Register an external failure source implementation.

        This allows plugins to provide their own cluster failure tracking.
        Only one external failure source can be registered at a time.

        Args:
            get_failures: Function to get active cluster failures.
                Signature: (cluster_hash: Optional[str],
                            cluster_name: Optional[str])
                           -> List[Dict[str, Any]]
                Returns list of dicts with keys: cluster_hash, failure_mode,
                failure_reason, cleared_at.
            clear_failures: Function to clear cluster failures.
                Signature: (cluster_hash: Optional[str],
                            cluster_name: Optional[str])
                           -> List[Dict[str, Any]]
                Returns list of dicts of the failures that were cleared.
        """
        cls._get_func = get_failures
        cls._clear_func = clear_failures
        logger.debug('Registered external failure source')

    @classmethod
    def is_registered(cls) -> bool:
        """Check if an external failure source is registered."""
        return cls._get_func is not None and cls._clear_func is not None

    @classmethod
    def get(cls,
            cluster_hash: Optional[str] = None,
            cluster_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get active cluster failures from the registered failure source.

        Args:
            cluster_hash: Hash of the cluster to query failures for.
            cluster_name: Name of the cluster to query failures for.

        Returns:
            List of dictionaries containing failure records.
            Each dict contains: cluster_hash, failure_mode, failure_reason,
            cleared_at. Returns empty list if no failure source is registered.
        """
        if cls._get_func is None:
            return []
        try:
            # pylint: disable=not-callable
            return cls._get_func(cluster_name=cluster_name,
                                 cluster_hash=cluster_hash)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get cluster failures: {e}')
            return []

    @classmethod
    def clear(cls,
              cluster_hash: Optional[str] = None,
              cluster_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Clear cluster failures via the registered failure source.

        Args:
            cluster_hash: Hash of the cluster to clear failures for.
            cluster_name: Name of the cluster to clear failures for.

        Returns:
            List of dictionaries containing the failure records that were
            cleared. Returns empty list if no failure source is registered.
        """
        if cls._clear_func is None:
            return []
        try:
            # pylint: disable=not-callable
            return cls._clear_func(cluster_name=cluster_name,
                                   cluster_hash=cluster_hash)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to clear cluster failures: {e}')
            return []
