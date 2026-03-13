"""Utility functions for the CLI."""
from typing import Dict, List, Optional, Tuple

from sky import jobs as managed_jobs
from sky.schemas.api import responses
from sky.server import common as server_common


def get_managed_job_queue(
    refresh: bool,
    skip_finished: bool = False,
    all_users: bool = False,
    job_ids: Optional[List[int]] = None,
    limit: Optional[int] = None,
    fields: Optional[List[str]] = None,
) -> server_common.RequestId[Tuple[List[responses.ManagedJobRecord], int, Dict[
        str, int], int]]:
    """Gets statuses of managed jobs.

    Please refer to sky.cli.job_queue for documentation.

    Args:
        refresh: Whether to restart the jobs controller if it is stopped.
        skip_finished: Whether to skip finished jobs.
        all_users: Whether to show all users' jobs.
        job_ids: IDs of the managed jobs to show.
        limit: Number of jobs to show.
        fields: Fields to get for the managed jobs.

    Returns:
        The request ID of the queue request.

    Request Raises:
        sky.exceptions.ClusterNotUpError: the jobs controller is not up or
          does not exist.
        RuntimeError: if failed to get the managed jobs with ssh.
    """
    return managed_jobs.queue_v2(refresh, skip_finished, all_users, job_ids,
                                 limit, fields)
