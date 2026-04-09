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
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = None,
    *,
    name_match: Optional[str] = None,
    pool_match: Optional[str] = None,
    user_match: Optional[str] = None,
    workspace_match: Optional[str] = None,
    statuses: Optional[List[str]] = None,
    page: Optional[int] = None,
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
        sort_by: Field to sort by (e.g., 'job_id', 'name', 'submitted_at').
        sort_order: Sort direction ('asc' or 'desc').
        name_match: Filter jobs by name (substring match).
        pool_match: Filter jobs by pool name (substring match).
        user_match: Filter jobs by user (substring match).
        workspace_match: Filter jobs by workspace (substring match).
        statuses: Filter jobs by statuses.
        page: Page number for pagination.

    Returns:
        - the request ID of the queue request
        - the version of the queue result

    Request Raises:
        sky.exceptions.ClusterNotUpError: the jobs controller is not up or
          does not exist.
        RuntimeError: if failed to get the managed jobs with ssh.
    """
    # These args are only supported by queue_v2. Collect them so we can
    # raise if we have to fall back to the v1 endpoint.
    v2_only_kwargs = dict(
        sort_by=sort_by,
        sort_order=sort_order,
        name_match=name_match,
        pool_match=pool_match,
        user_match=user_match,
        workspace_match=workspace_match,
        statuses=statuses,
        page=page,
    )
    try:
        return typing.cast(
            server_common.RequestId[
                Union[List[responses.ManagedJobRecord],
                      Tuple[List[responses.ManagedJobRecord], int,
                            Dict[str, int], int]]],
            managed_jobs.queue_v2(refresh,
                                  skip_finished,
                                  all_users,
                                  job_ids,
                                  limit,
                                  fields,
                                  sort_by,
                                  sort_order,
                                  name_match=name_match,
                                  pool_match=pool_match,
                                  user_match=user_match,
                                  workspace_match=workspace_match,
                                  statuses=statuses,
                                  page=page)), QueueResultVersion.V2
    except exceptions.APINotSupportedError as e:
        used_v2_args = [k for k, v in v2_only_kwargs.items() if v is not None]
        if used_v2_args:
            raise exceptions.APINotSupportedError(
                f'The following filter/sort options require queue_v2 which '
                f'is not supported by the API server: '
                f'{", ".join(used_v2_args)}. '
                f'Please upgrade the API server.') from e
        return typing.cast(
            server_common.RequestId[
                Union[List[responses.ManagedJobRecord],
                      Tuple[List[responses.ManagedJobRecord], int,
                            Dict[str, int], int]]],
            managed_jobs.queue(refresh, skip_finished, all_users,
                               job_ids)), QueueResultVersion.V1
