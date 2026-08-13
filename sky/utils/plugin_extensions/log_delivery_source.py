"""External log delivery source interface for plugins.

When a logging agent forwards job logs to an external store and a reader
can stream them back, the store is the durable copy and the controller
skips keeping a local one. That decision is made from the server's own
config -- it only knows that *a* logging agent is configured, not that this
particular job's logs actually reached the store. An agent that could not
be deployed on the target cluster, or that was still starting when a short
job finished, leaves the job with no readable logs at all: none in the
store, and none on the controller either.

This extension point lets whatever component deploys and operates the
logging agent report, per cluster, whether it is actually delivering that
cluster's logs. Core SkyPilot consults it before dropping the local copy:
an unconfirmed delivery keeps the copy (and logs why), a confirmed one is
skipped as before. Not registering a source keeps the previous behavior.

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

    A plugin registers its implementation during install(); core SkyPilot
    asks it whether a cluster's logs reached the external store without
    knowing which plugin (if any) provides the answer.

    The answer is deliberately one-sided: a source reports only the case it
    can prove -- that the logs did *not* make it. Everything else (no source
    registered, no record for the cluster, the check itself failing) reads
    as "no reason to doubt delivery" and preserves the previous behavior,
    so a broken source degrades to today's semantics rather than making
    every job download its logs again.

    Implementations must answer quickly and must not block. The check runs
    once per managed job task, on the job-finalization path, in a worker
    thread drawn from a pool the rest of the controller shares -- so a
    source that hangs does not just delay one job, it holds a thread others
    are waiting for. Exceptions are contained (see below); a hang is not.
    A local lookup of something recorded earlier is the intended shape; a
    round trip that can stall indefinitely is not.
    """

    _undelivered_reason_func: Optional[UndeliveredReasonFunc] = None

    @classmethod
    def register(cls, undelivered_reason: UndeliveredReasonFunc) -> None:
        """Register an external log delivery source implementation.

        Only one delivery source can be registered at a time.

        Args:
            undelivered_reason: Function returning a human-readable reason
                why the cluster's logs did not reach the external store, or
                None when there is no evidence against delivery. Must return
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
