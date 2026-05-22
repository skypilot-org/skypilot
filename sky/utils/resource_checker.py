"""Resource checking utilities for finding active clusters and managed jobs."""

import concurrent.futures
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

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

    all_clusters, all_managed_jobs = _get_active_resources()

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
            if resource_type == 'user':
                # resource_name is user_id
                user_info = global_user_state.get_user(resource_name)
                if user_info and user_info.name:
                    resource_name = user_info.name
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


def check_users_workspaces_active_resources(
        user_ids: List[str],
        workspace_names: List[str]) -> Tuple[str, List[str], Dict[str, str]]:
    """Check if all the active clusters or managed jobs in workspaces
       belong to the user_ids. If not, return the error message.

    Args:
        user_ids: List of user_id.
        workspace_names: List of workspace_name.

    Returns:
        resource_error_summary: str
        missed_users_names: List[str]
        missed_user_dict: Dict[str, str]
    """
    all_clusters, all_managed_jobs = _get_active_resources_for_workspaces(
        workspace_names)
    resource_errors = []
    missed_users = set()
    active_cluster_names = []
    active_job_names = []
    # Check clusters
    if all_clusters:
        for cluster in all_clusters:
            user_hash = cluster.get('user_hash')
            if user_hash and user_hash not in user_ids:
                missed_users.add(user_hash)
                active_cluster_names.append(cluster['name'])
        if active_cluster_names:
            cluster_list = ', '.join(active_cluster_names)
            resource_errors.append(
                f'{len(active_cluster_names)} active cluster(s):'
                f' {cluster_list}')

    # Check managed jobs
    if all_managed_jobs:
        for job in all_managed_jobs:
            user_hash = job.get('user_hash')
            if user_hash and user_hash not in user_ids:
                missed_users.add(user_hash)
                active_job_names.append(str(job['job_id']))
        if active_job_names:
            job_list = ', '.join(active_job_names)
            resource_errors.append(f'{len(active_job_names)} active'
                                   f' managed job(s): {job_list}')

    resource_error_summary = ''
    if resource_errors:
        resource_error_summary = ' and '.join(resource_errors)
    missed_users_names = []
    missed_user_dict = {}
    if missed_users:
        all_users = global_user_state.get_all_users()
        for user in all_users:
            if user.id in missed_users:
                missed_users_names.append(user.name if user.name else user.id)
                missed_user_dict[user.id] = user.name if user.name else user.id
    return resource_error_summary, missed_users_names, missed_user_dict


def _get_active_resources_for_workspaces(
    workspace_names: List[str]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Get active clusters or managed jobs for workspaces.

    Args:
        workspace_names: List of workspace_name.

    Returns:
        all_clusters: List[Dict[str, Any]]
        all_managed_jobs: List[Dict[str, Any]]
    """
    if not workspace_names:
        return [], []

    def filter_by_workspaces(workspace_names: List[str]):
        return lambda resource: (resource.get(
            'workspace', constants.SKYPILOT_DEFAULT_WORKSPACE) in
                                 workspace_names)

    return _get_active_resources_by_names(workspace_names, filter_by_workspaces)


def _get_active_resources_by_names(
    resource_names: List[str],
    filter_factory: Callable[[List[str]], Callable[[Dict[str, Any]], bool]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Get active clusters or managed jobs.

    Args:
        resource_names: List of resource_name.
        filter_factory: Function that takes a resource_name and returns a filter
            function for clusters/jobs.

    Returns:
        all_clusters: List[Dict[str, Any]]
        all_managed_jobs: List[Dict[str, Any]]
    """

    all_clusters, all_managed_jobs = _get_active_resources()

    resource_clusters = []
    resource_active_jobs = []

    # Check each resource against the fetched data,
    # return the active resources by names
    resource_filter = filter_factory(resource_names)

    # Filter clusters for this resource
    if all_clusters:
        resource_clusters = [
            cluster for cluster in all_clusters if resource_filter(cluster)
        ]

    # Filter managed jobs for this resource
    if all_managed_jobs:
        resource_active_jobs = [
            job for job in all_managed_jobs if resource_filter(job)
        ]

    return resource_clusters, resource_active_jobs


def check_user_role_demotion(
        user_id: str,
        remaining_admin_user_ids: Optional[Set[str]] = None) -> None:
    """Check whether an admin can be safely demoted to a regular user.

    After demotion the user loses implicit access to all private workspaces
    where they are not listed in ``allowed_users``. This function ensures
    the user has no active clusters or managed jobs in private workspaces
    they would lose access to.

    Args:
        user_id: The ID of the user being demoted.
        remaining_admin_user_ids: Optional pre-computed set of user IDs that
            will remain admins after the demotion. If not provided, it is
            computed from the casbin policy.

    Raises:
        ValueError: If the user has active clusters or managed jobs in
            private workspaces they would lose access to.
    """
    # Imports done lazily to avoid circular imports with permission/workspaces.
    # pylint: disable=import-outside-toplevel
    from sky import skypilot_config
    from sky.users import permission
    from sky.users import rbac
    from sky.workspaces import utils as workspaces_utils

    workspaces = skypilot_config.get_nested(('workspaces',), default_value={})
    if not workspaces:
        return

    if remaining_admin_user_ids is None:
        remaining_admin_user_ids = set(
            permission.permission_service.get_users_for_role(
                rbac.RoleName.ADMIN.value))
        remaining_admin_user_ids.discard(user_id)

    inaccessible_workspaces: List[str] = []
    for workspace_name, workspace_config in workspaces.items():
        if not workspace_config.get('private', False):
            continue
        allowed_user_ids = set(
            workspaces_utils.get_workspace_users(workspace_config))
        if (user_id in allowed_user_ids or user_id in remaining_admin_user_ids):
            continue
        inaccessible_workspaces.append(workspace_name)

    if not inaccessible_workspaces:
        return

    all_clusters, all_managed_jobs = _get_active_resources()
    workspace_set = set(inaccessible_workspaces)

    workspace_resources: Dict[str, Dict[str, List[str]]] = {}
    for cluster in all_clusters:
        if cluster.get('user_hash') != user_id:
            continue
        ws = cluster.get('workspace', constants.SKYPILOT_DEFAULT_WORKSPACE)
        if ws not in workspace_set:
            continue
        workspace_resources.setdefault(ws, {'clusters': [], 'jobs': []})
        workspace_resources[ws]['clusters'].append(cluster['name'])
    for job in all_managed_jobs:
        if job.get('user_hash') != user_id:
            continue
        ws = job.get('workspace', constants.SKYPILOT_DEFAULT_WORKSPACE)
        if ws not in workspace_set:
            continue
        workspace_resources.setdefault(ws, {'clusters': [], 'jobs': []})
        workspace_resources[ws]['jobs'].append(str(job['job_id']))

    if not workspace_resources:
        return

    user_info = global_user_state.get_user(user_id)
    user_display = (user_info.name if user_info and user_info.name else user_id)

    error_lines = []
    for ws, res in workspace_resources.items():
        parts = []
        if res['clusters']:
            n_clusters = len(res['clusters'])
            cluster_list = ', '.join(res['clusters'])
            parts.append(f'{n_clusters} active cluster(s): {cluster_list}')
        if res['jobs']:
            n_jobs = len(res['jobs'])
            job_list = ', '.join(res['jobs'])
            parts.append(f'{n_jobs} active managed job(s): {job_list}')
        joined = ' and '.join(parts)
        error_lines.append(f'  - workspace {ws!r}: {joined}')

    raise ValueError(
        f'Cannot demote user {user_display!r} from admin to user because '
        f'they have active resources in private workspaces where they are '
        f'not in allowed_users:\n' + '\n'.join(error_lines) +
        '\nPlease either terminate these resources or add the user to the '
        'allowed_users of those workspaces first.')


def _get_active_resources(
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Get all active clusters and managed jobs.

    Returns:
        all_clusters: List[Dict[str, Any]]
        all_managed_jobs: List[Dict[str, Any]]
    """

    def get_all_clusters() -> List[Dict[str, Any]]:
        return global_user_state.get_clusters()

    def get_all_managed_jobs() -> List[Dict[str, Any]]:
        # pylint: disable=import-outside-toplevel
        from sky.jobs.server import core as managed_jobs_core
        try:
            filtered_jobs, _, _, _ = managed_jobs_core.queue_v2(
                refresh=False,
                skip_finished=True,
                all_users=True,
                fields=['job_id', 'user_hash', 'workspace'])
            return filtered_jobs
        except exceptions.ClusterNotUpError:
            logger.warning('All jobs should be finished.')
            return []

    # Fetch both clusters and jobs in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        clusters_future = executor.submit(get_all_clusters)
        jobs_future = executor.submit(get_all_managed_jobs)

        all_clusters = clusters_future.result()
        all_managed_jobs = jobs_future.result()

    return all_clusters, all_managed_jobs
