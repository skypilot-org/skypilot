"""Interface for reading job logs back from an external logging store.

When a job's cluster is stopped or terminated, its logs can no longer be read
from the cluster. If the logs were forwarded to an external store, a registered
``LogReader`` streams them back. Register one with ``register_log_reader()``.
"""
import abc
from typing import Optional


class LogReader(abc.ABC):
    """Reads job logs back from an external logging store."""

    @abc.abstractmethod
    def read_cluster_job_logs(self, cluster_name: str, job_id: Optional[int], *,
                              follow: bool, tail: int) -> Optional[int]:
        """Streams a cluster job's logs from the external store to stdout.

        Args:
            cluster_name: The display name of the cluster the job ran on.
            job_id: The job id, or None for the latest job.
            follow: Whether to follow the log. An external store is historical,
                so this may be treated as a no-op.
            tail: Number of lines from the end to stream; 0 means all.

        Returns:
            The exit code to report for the job if logs were streamed (matching
            tail_logs' return value), or None if no logs were produced, in which
            case the caller raises its original error. A reader that cannot
            determine the job's status from the store should return 0.
        """
        raise NotImplementedError


_log_reader: Optional[LogReader] = None


def register_log_reader(reader: LogReader) -> None:
    """Registers the process-global external log reader."""
    global _log_reader
    _log_reader = reader


def get_log_reader() -> Optional[LogReader]:
    """Returns the registered external log reader, or None if unset."""
    return _log_reader
