"""Resource checking utilities for finding active clusters and managed jobs."""

import concurrent.futures
from typing import Any, Callable, Dict, List, Tuple

from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky.skylet import constants

logger = sky_logging.init_logger(__name__)


def check_no_active_resources_for_users(
        user_operations: List[Tuple[str, str]]) -> None:
    """Check if users have active clusters or managed jobs.

    Args:
        user_operations: List of tuples (user_id, operation) where
            operation is 'update' or 'delete'.

    Raises:
        ValueError: If any user has active clusters or managed jobs.
            The error message will include all users with issues.
    """
    if not user_operations:
        return

    def filter_by_user(user_id: str):
        return lambda resource: resource.get('user_hash') == user_id

    _check_active_resources(user_operations, filter_by_user, 'user')


def check_no_active_resources_for_workspaces(
        workspace_operations: List[Tuple[str, str]]) -> None:
    """Check if workspaces have active clusters or managed jobs.

    Args:
        workspace_operations: List of tuples (workspace_name, operation) where
            operation is 'update' or 'delete'.

    Raises:
        ValueError: If any workspace has active clusters or managed jobs.
            The error message will include all workspaces with issues.
    """
    if not workspace_operations:
        return

    def filter_by_workspace(workspace_name: str):
        return lambda resource: (resource.get(
            'workspace', constants.SKYPILOT_DEFAULT_WORKSPACE) == workspace_name
                                )

    _check_active_resources(workspace_operations, filter_by_workspace,
                            'workspace')


def _check_active_resources(resource_operations: List[Tuple[str, str]],
                            filter_factory: Callable[[str],
                                                     Callable[[Dict[str, Any]],
                                                              bool]],
                            resource_type: str) -> None:
    """Check if resource entities have active clusters or managed jobs.

    Args:
        resource_operations: List of tuples (resource_name, operation) where
            operation is 'update' or 'delete'.
        filter_factory: Function that takes a resource_name and returns a filter
            function for clusters/jobs.
        resource_type: Type of resource being checked ('user' or 'workspace').

    Raises:
        ValueError: If any resource has active clusters or managed jobs.
    """

    def get_all_clusters():
        return global_user_state.get_clusters()

    def get_all_managed_jobs():
        # pylint: disable=import-outside-toplevel
        from sky.jobs.server import core as managed_jobs_core
        try:
            return managed_jobs_core.queue(refresh=False,
                                           skip_finished=True,
                                           all_users=True)
        except exceptions.ClusterNotUpError:
            logger.warning('All jobs should be finished.')
            return []

    # Fetch both clusters and jobs in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        clusters_future = executor.submit(get_all_clusters)
        jobs_future = executor.submit(get_all_managed_jobs)

        all_clusters = clusters_future.result()
        all_managed_jobs = jobs_future.result()

    # Collect all error messages instead of raising immediately
    error_messages = []

    # Check each resource against the fetched data
    for resource_name, operation in resource_operations:
        resource_filter = filter_factory(resource_name)

        # Filter clusters for this resource
        resource_clusters = [
            cluster for cluster in all_clusters if resource_filter(cluster)
        ]

        # Filter managed jobs for this resource
        resource_active_jobs = [
            job for job in all_managed_jobs if resource_filter(job)
        ]

        # Collect error messages for this resource
        resource_errors = []

        if resource_clusters:
            active_cluster_names = [
                cluster['name'] for cluster in resource_clusters
            ]
            cluster_list = ', '.join(active_cluster_names)
            resource_errors.append(
                f'{len(resource_clusters)} active cluster(s): {cluster_list}')

        if resource_active_jobs:
            job_names = [str(job['job_id']) for job in resource_active_jobs]
            job_list = ', '.join(job_names)
            resource_errors.append(
                f'{len(resource_active_jobs)} active managed job(s): '
                f'{job_list}')

        # If this resource has issues, add to overall error messages
        if resource_errors:
            resource_error_summary = ' and '.join(resource_errors)
            error_messages.append(
                f'Cannot {operation} {resource_type} {resource_name!r} '
                f'because it has {resource_error_summary}.')

    # If we collected any errors, raise them all together
    if error_messages:
        if len(error_messages) == 1:
            # Single resource error
            full_message = error_messages[
                0] + ' Please terminate these resources first.'
        else:
            # Multiple resource errors
            full_message = (f'Cannot proceed due to active resources in '
                            f'{len(error_messages)} {resource_type}(s):\n' +
                            '\n'.join(f'• {msg}' for msg in error_messages) +
                            '\nPlease terminate these resources first.')
        raise ValueError(full_message)
