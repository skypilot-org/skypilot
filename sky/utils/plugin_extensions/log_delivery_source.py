"""External log delivery source interface for plugins.

When a logging agent is configured for a cluster's placement and a log
reader is registered, the controller skips keeping a local copy of a job's
logs, and a followed tail of a running managed job reads the external store
instead of the cluster. Those decisions are made from the server's config
alone, not from whether a given cluster's logs were actually delivered.

This extension point lets a registered source report, per cluster, a
reason the logs were not delivered. Core SkyPilot consults it before
dropping the local copy and before following a running job from the store:
a reported reason keeps the copy (and logs why) and streams the running job
from its cluster; no reason skips the copy and follows the store. Not
registering a source keeps the previous behavior.

Example usage in a plugin:
    from sky.utils.plugin_extensions import LogDeliverySource

    # Register a custom delivery source
    LogDeliverySource.register(undelivered_reason=my_undelivered_reason)

Example usage in core SkyPilot:
    from sky.utils.plugin_extensions import LogDeliverySource

    reason = LogDeliverySource.undelivered_reason(
        cluster_name='my-cluster', cluster_name_on_cloud='my-cluster-abcd')
    if reason is not None:
        ...  # keep a local copy of the logs
"""
from typing import Optional, Protocol

from sky import sky_logging

logger = sky_logging.init_logger(__name__)


class UndeliveredReasonFunc(Protocol):
    """Protocol for the undelivered_reason function."""

    def __call__(self,
                 cluster_name: Optional[str] = None,
                 cluster_name_on_cloud: Optional[str] = None) -> Optional[str]:
        ...


class LogDeliverySource:
    """Singleton class for the external log delivery source.

    An implementation is registered during a plugin's install(); core
    SkyPilot queries it without knowing which plugin (if any) provides it.

    The answer is deliberately one-sided: a source reports only that the
    logs were *not* delivered. Everything else (no source registered, a
    source returning None, the check itself failing) reads as delivered and
    preserves the previous behavior, so a broken source degrades to the
    default semantics. For the same reason, a source should return a reason
    while it does not yet know whether a cluster's logs are delivered (e.g.
    early in a run, before it has a record for the cluster), rather than
    None: otherwise a viewer may follow a store that has none of the logs.

    Implementations must answer quickly and must not block. The check runs
    at two points:
    - once per managed job task, on the job-finalization path, in a worker
      thread drawn from a pool the rest of the controller shares;
    - when a followed tail of a running managed job task starts, once per
      viewer, in an API server request thread.
    A source that hangs does not just delay one job or viewer, it holds a
    thread others are waiting for. Exceptions are contained (see below); a
    hang is not.
    """

    _undelivered_reason_func: Optional[UndeliveredReasonFunc] = None

    @classmethod
    def register(cls, undelivered_reason: UndeliveredReasonFunc) -> None:
        """Register an external log delivery source implementation.

        Only one delivery source can be registered at a time.

        Args:
            undelivered_reason: Function returning a human-readable reason
                why the cluster's logs did not (or may not yet) reach the
                external store, or None when they are delivered. Must return
                promptly and must not block -- see the class docstring.
                Signature: (cluster_name: Optional[str],
                            cluster_name_on_cloud: Optional[str])
                           -> Optional[str]
        """
        cls._undelivered_reason_func = undelivered_reason
        logger.debug('Registered external log delivery source')

    @classmethod
    def is_registered(cls) -> bool:
        """Check if a log delivery source is registered."""
        return cls._undelivered_reason_func is not None

    @classmethod
    def undelivered_reason(
            cls,
            cluster_name: Optional[str] = None,
            cluster_name_on_cloud: Optional[str] = None) -> Optional[str]:
        """Why the cluster's logs did not reach the external log store.

        Args:
            cluster_name: Display name of the cluster the job ran on.
            cluster_name_on_cloud: Provider-side name of that cluster.

        Returns:
            A human-readable reason, or None if the logs are believed to
            have been delivered -- which is also what an unregistered or
            failing source returns.
        """
        if cls._undelivered_reason_func is None:
            return None
        try:
            # pylint: disable=not-callable
            return cls._undelivered_reason_func(
                cluster_name=cluster_name,
                cluster_name_on_cloud=cluster_name_on_cloud)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to check external log delivery: {e}')
            return None
