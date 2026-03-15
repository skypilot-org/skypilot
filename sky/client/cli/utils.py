"""Utility functions for the CLI."""
import enum
import typing
from typing import Dict, List, Optional, Tuple, Union

from sky import exceptions
from sky import jobs as managed_jobs
from sky.schemas.api import responses
from sky.server import common as server_common


class QueueResultVersion(enum.Enum):
    """The version of the queue result.

    V1: The old version of the queue result.
        - job_records (List[responses.ManagedJobRecord]): A list of dicts,
           with each dict containing the information of a job.
    V2: The new version of the queue result.
        - job_records (List[responses.ManagedJobRecord]): A list of dicts,
           with each dict containing the information of a job.
        - total (int): Total number of jobs after filter.
        - status_counts (Dict[str, int]): Status counts after filter.
        - total_no_filter (int): Total number of jobs before filter.
    """
    V1 = 'v1'
    V2 = 'v2'

    def v2(self) -> bool:
        return self == QueueResultVersion.V2


def get_managed_job_queue(
    refresh: bool,
    skip_finished: bool = False,
    all_users: bool = False,
    job_ids: Optional[List[int]] = None,
    limit: Optional[int] = None,
    fields: Optional[List[str]] = None,
) -> Tuple[server_common.RequestId[Union[List[responses.ManagedJobRecord],
                                         Tuple[List[responses.ManagedJobRecord],
                                               int, Dict[str, int], int]]],
           QueueResultVersion]:
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
        - the request ID of the queue request
        - the version of the queue result

    Request Raises:
        sky.exceptions.ClusterNotUpError: the jobs controller is not up or
          does not exist.
        RuntimeError: if failed to get the managed jobs with ssh.
    """
    try:
        return typing.cast(
            server_common.RequestId[
                Union[List[responses.ManagedJobRecord],
                      Tuple[List[responses.ManagedJobRecord], int,
                            Dict[str, int], int]]],
            managed_jobs.queue_v2(refresh, skip_finished, all_users, job_ids,
                                  limit, fields)), QueueResultVersion.V2
    except exceptions.APINotSupportedError:
        return typing.cast(
            server_common.RequestId[
                Union[List[responses.ManagedJobRecord],
                      Tuple[List[responses.ManagedJobRecord], int,
                            Dict[str, int], int]]],
            managed_jobs.queue(refresh, skip_finished, all_users,
                               job_ids)), QueueResultVersion.V1
