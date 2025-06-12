"""SkyPilot logs base module."""
import abc
from typing import List, NamedTuple


class LogLabels(NamedTuple):
    """Log labels of the log line.
    
    This will be used to identify the log stream of different logging entities.
    The layout of the log streams in external logging store is an
    implementation of SkyPilot and is subject to change.

    TODO(aylei): for now we only support writing logs to the external logging
    store, but we eventually need to retrieve the log back with our SDK, e.g.
    `sky logs`, to hide the implementation details of the external logging
    store.
    """
    # Unique hash of the SkyPilot cluster that emitted the log.
    # The hash is expected to be unique even across different SkyPilot API
    # server instances.
    cluster_hash: str
    # Name of the cluster, technically this is not necessary to identify a
    # log stream, but it's useful for debugging and enables better visibility
    # in the external logging store.
    cluster_name: str
    # Identifies the job that emitted the log, unique within a cluster.
    job_id: str
    # Managed job ID, for visibility in the external logging store.
    managed_job_id: str
    # Job name, for visibility in the external logging store.
    job_name: str

class LogStore(abc.ABC):
    """Log store base class."""

    @abc.abstractmethod
    def write(self, lines: List[str], labels: LogLabels) -> None:
        """Write log to the store.
        
        Args:
            lines: The log lines to write, it is recommended to write multiple
                log lines in a single call since the underlying implementation 
                can leverage batching to improve performance.
            labels: The labels of the log line.
        """
        pass
