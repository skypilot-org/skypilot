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

    def read_managed_job_logs(self,
                              job_id: int,
                              task_id: Optional[int],
                              *,
                              task_name: Optional[str] = None,
                              follow: bool,
                              tail: int) -> Optional[int]:
        """Streams a managed job task's logs from the external store to stdout.

        Addresses logs by the managed job's identity instead of the cluster the
        job ran on, for runtimes whose forwarded log records carry the managed
        job id rather than an on-cluster job id (so ``read_cluster_job_logs``
        cannot locate them). Non-abstract: readers without managed-job
        addressing inherit the default and return None; callers then rely on
        ``read_cluster_job_logs`` alone.

        Note on identity: a managed job id is only unique within one API
        server -- every server mints ids from 1, and a rebuilt jobs database
        restarts the sequence. A store shared by more than one deployment (or
        reused across a database rebuild, within its retention window) can
        therefore hold several jobs under the same id. Implementations should
        narrow the query with whatever additional identity their records carry
        (``task_name``, an owner hash, a deployment label) rather than trusting
        the id alone.

        Args:
            job_id: The managed job id.
            task_id: The task id within the job, or None for all tasks.
            task_name: The task's name, when the caller knows it. Part of the
                job's identity in some record layouts; see the note above.
            follow: Whether to follow the log. An external store is historical,
                so this may be treated as a no-op.
            tail: Number of lines from the end to stream; 0 means all.

        Returns:
            The exit code to report if logs were streamed, or None if this
            reader does not support managed-job addressing or found no records
            for the job. A reader that cannot determine the job's status from
            the store should return 0.
        """
        del job_id, task_id, task_name, follow, tail  # Unsupported by default.
        return None


_log_reader: Optional[LogReader] = None


def register_log_reader(reader: LogReader) -> None:
    """Registers the process-global external log reader."""
    global _log_reader
    _log_reader = reader


def get_log_reader() -> Optional[LogReader]:
    """Returns the registered external log reader, or None if unset."""
    return _log_reader
