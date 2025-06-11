"""Workspace management core."""

import concurrent.futures
from typing import Any, Callable, Dict, List

import filelock

from sky import check as sky_check
from sky import exceptions
from sky import global_user_state
from sky import models
from sky import sky_logging
from sky import skypilot_config
from sky.skylet import constants
from sky.usage import usage_lib
from sky.users import permission
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import schemas
from sky.workspaces import utils as workspaces_utils

logger = sky_logging.init_logger(__name__)

# Lock for workspace configuration updates to prevent race conditions
_WORKSPACE_CONFIG_LOCK_TIMEOUT_SECONDS = 60

# =========================
# = Workspace Management =
# =========================


def get_workspaces() -> Dict[str, Any]:
    """Returns the workspace config."""
    return workspaces_for_user(common_utils.get_current_user().id)


def _update_workspaces_config(
        workspace_modifier_fn: Callable[[Dict[str, Any]],
                                        None]) -> Dict[str, Any]:
    """Update the workspaces configuration in the config file.

    This function uses file locking to prevent race conditions when multiple
    processes try to update the workspace configuration simultaneously.

    Args:
        workspace_modifier_fn: A function that takes the current workspaces
            dict and modifies it in-place. This ensures all read-modify-write
            operations happen atomically inside the lock.

    Returns:
        The updated workspaces configuration.
    """
    lock_path = skypilot_config.get_skypilot_config_lock_path()
    try:
        with filelock.FileLock(lock_path,
                               _WORKSPACE_CONFIG_LOCK_TIMEOUT_SECONDS):
            # Read the current config inside the lock to ensure we have
            # the latest state
            current_config = skypilot_config.to_dict()
            current_workspaces = current_config.get('workspaces', {}).copy()

            # Apply the modification inside the lock
            workspace_modifier_fn(current_workspaces)

            # Update the config with the modified workspaces
            current_config['workspaces'] = current_workspaces

            # Write the configuration back to the file
            skypilot_config.update_api_server_config_no_lock(current_config)

            return current_workspaces
    except filelock.Timeout as e:
        raise RuntimeError(
            f'Failed to update workspace configuration due to a timeout '
            f'when trying to acquire the lock at {lock_path}. This may '
            'indicate another SkyPilot process is currently updating the '
            'configuration. Please try again or manually remove the lock '
            f'file if you believe it is stale.') from e


def _check_workspace_has_no_active_resources(workspace_name: str,
                                             operation: str) -> None:
    """Check if a workspace has active clusters or managed jobs.

    Args:
        workspace_name: The name of the workspace to check.
        operation: The operation being performed ('update' or 'delete').

    Raises:
        ValueError: If the workspace has active clusters or managed jobs.
    """
    _check_workspaces_have_no_active_resources([(workspace_name, operation)])


def _check_workspaces_have_no_active_resources(
        workspace_operations: list) -> None:
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
            logger.warning('All jobs should be finished in workspace.')
            return []

    # Fetch both clusters and jobs in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        clusters_future = executor.submit(get_all_clusters)
        jobs_future = executor.submit(get_all_managed_jobs)

        all_clusters = clusters_future.result()
        all_managed_jobs = jobs_future.result()

    # Collect all error messages instead of raising immediately
    error_messages = []

    # Check each workspace against the fetched data
    for workspace_name, operation in workspace_operations:
        # Filter clusters for this workspace
        workspace_clusters = [
            cluster for cluster in all_clusters
            if (cluster.get('workspace', constants.SKYPILOT_DEFAULT_WORKSPACE)
                == workspace_name)
        ]

        # Filter managed jobs for this workspace
        workspace_active_jobs = [
            job for job in all_managed_jobs
            if job.get('workspace', constants.SKYPILOT_DEFAULT_WORKSPACE) ==
            workspace_name
        ]

        # Collect error messages for this workspace
        workspace_errors = []

        if workspace_clusters:
            active_cluster_names = [
                cluster['name'] for cluster in workspace_clusters
            ]
            cluster_list = ', '.join(active_cluster_names)
            workspace_errors.append(
                f'{len(workspace_clusters)} active cluster(s): {cluster_list}')

        if workspace_active_jobs:
            job_names = [str(job['job_id']) for job in workspace_active_jobs]
            job_list = ', '.join(job_names)
            workspace_errors.append(
                f'{len(workspace_active_jobs)} active managed job(s): '
                f'{job_list}')

        # If this workspace has issues, add to overall error messages
        if workspace_errors:
            workspace_error_summary = ' and '.join(workspace_errors)
            error_messages.append(
                f'Cannot {operation} workspace {workspace_name!r} because it '
                f'has {workspace_error_summary}.')

    # If we collected any errors, raise them all together
    if error_messages:
        if len(error_messages) == 1:
            # Single workspace error
            full_message = error_messages[
                0] + ' Please terminate these resources first.'
        else:
            # Multiple workspace errors
            full_message = (f'Cannot proceed due to active resources in '
                            f'{len(error_messages)} workspace(s):\n' +
                            '\n'.join(f'• {msg}' for msg in error_messages) +
                            '\nPlease terminate these resources first.')
        raise ValueError(full_message)


def _validate_workspace_config(workspace_name: str,
                               workspace_config: Dict[str, Any]) -> None:
    """Validate the workspace configuration.
    """
    workspace_schema = schemas.get_config_schema(
    )['properties']['workspaces']['additionalProperties']
    try:
        common_utils.validate_schema(
            workspace_config, workspace_schema,
            f'Invalid configuration for workspace {workspace_name!r}: ')
    except exceptions.InvalidSkyPilotConfigError as e:
        # We need to replace this exception with a ValueError because: a) it is
        # more user-friendly and b) it will not be caught by the try-except by
        # the caller the may cause confusion.
        raise ValueError(str(e)) from e


@usage_lib.entrypoint
def update_workspace(workspace_name: str, config: Dict[str,
                                                       Any]) -> Dict[str, Any]:
    """Updates a specific workspace configuration.

    Args:
        workspace_name: The name of the workspace to update.
        config: The new configuration for the workspace.

    Returns:
        The updated workspaces configuration.

    Raises:
        ValueError: If the workspace configuration is invalid, or if there are
            active clusters or managed jobs in the workspace.
        FileNotFoundError: If the config file cannot be found.
        PermissionError: If the config file cannot be written.
    """
    _validate_workspace_config(workspace_name, config)

    # Check for active clusters and managed jobs in the workspace
    # TODO(zhwu): we should allow the edits that only contain changes to
    # allowed_users or private.
    _check_workspace_has_no_active_resources(workspace_name, 'update')

    def update_workspace_fn(workspaces: Dict[str, Any]) -> None:
        """Function to update workspace inside the lock."""
        workspaces[workspace_name] = config
        users = workspaces_utils.get_workspace_users(config)
        permission_service = permission.permission_service
        permission_service.update_workspace_policy(workspace_name, users)

    # Use the internal helper function to save
    result = _update_workspaces_config(update_workspace_fn)

    # Validate the workspace by running sky check for it
    try:
        sky_check.check(quiet=True, workspace=workspace_name)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Workspace {workspace_name} configuration saved but '
                       f'validation check failed: {e}')
        # Don't fail the update if the check fails, just warn

    return result


@usage_lib.entrypoint
def create_workspace(workspace_name: str, config: Dict[str,
                                                       Any]) -> Dict[str, Any]:
    """Creates a new workspace configuration.

    Args:
        workspace_name: The name of the workspace to create.
        config: The configuration for the new workspace.

    Returns:
        The updated workspaces configuration.

    Raises:
        ValueError: If the workspace already exists or configuration is invalid.
        FileNotFoundError: If the config file cannot be found.
        PermissionError: If the config file cannot be written.
    """
    # Validate the workspace name
    if not workspace_name or not isinstance(workspace_name, str):
        raise ValueError('Workspace name must be a non-empty string.')

    _validate_workspace_config(workspace_name, config)

    def create_workspace_fn(workspaces: Dict[str, Any]) -> None:
        """Function to create workspace inside the lock."""
        if workspace_name in workspaces:
            raise ValueError(f'Workspace {workspace_name!r} already exists. '
                             'Use update instead.')
        workspaces[workspace_name] = config
        # Add policy for the workspace and allowed users
        users = workspaces_utils.get_workspace_users(config)
        permission_service = permission.permission_service
        permission_service.add_workspace_policy(workspace_name, users)

    # Use the internal helper function to save
    result = _update_workspaces_config(create_workspace_fn)

    # Validate the workspace by running sky check for it
    try:
        sky_check.check(quiet=True, workspace=workspace_name)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Workspace {workspace_name} configuration saved but '
                       f'validation check failed: {e}')
        # Don't fail the update if the check fails, just warn

    return result


@usage_lib.entrypoint
def delete_workspace(workspace_name: str) -> Dict[str, Any]:
    """Deletes a workspace configuration.

    Args:
        workspace_name: The name of the workspace to delete.

    Returns:
        The updated workspaces configuration.

    Raises:
        ValueError: If the workspace doesn't exist, is the default workspace,
                   or has active clusters or managed jobs.
        FileNotFoundError: If the config file cannot be found.
        PermissionError: If the config file cannot be written.
    """
    # Prevent deletion of default workspace
    if workspace_name == constants.SKYPILOT_DEFAULT_WORKSPACE:
        raise ValueError(f'Cannot delete the default workspace '
                         f'{constants.SKYPILOT_DEFAULT_WORKSPACE!r}.')

    # Check if workspace exists
    current_workspaces = get_workspaces()
    if workspace_name not in current_workspaces:
        raise ValueError(f'Workspace {workspace_name!r} does not exist.')

    # Check for active clusters and managed jobs in the workspace
    _check_workspace_has_no_active_resources(workspace_name, 'delete')

    def delete_workspace_fn(workspaces: Dict[str, Any]) -> None:
        """Function to delete workspace inside the lock."""
        if workspace_name not in workspaces:
            raise ValueError(f'Workspace {workspace_name!r} does not exist.')
        del workspaces[workspace_name]
        permission_service = permission.permission_service
        permission_service.remove_workspace_policy(workspace_name)

    # Use the internal helper function to save
    return _update_workspaces_config(delete_workspace_fn)


# =========================
# = Config Management =
# =========================


@usage_lib.entrypoint
def get_config() -> Dict[str, Any]:
    """Returns the entire SkyPilot configuration.

    Returns:
        The complete SkyPilot configuration as a dictionary.
    """
    return skypilot_config.to_dict()


@usage_lib.entrypoint
def update_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Updates the entire SkyPilot configuration.

    Args:
        config: The new configuration to save.

    Returns:
        The updated configuration.

    Raises:
        ValueError: If the configuration is invalid, or if there are
            active clusters or managed jobs in workspaces being modified.
        FileNotFoundError: If the config file cannot be found.
        PermissionError: If the config file cannot be written.
    """
    # Validate the configuration using the schema
    try:
        common_utils.validate_schema(config, schemas.get_config_schema(),
                                     'Invalid SkyPilot configuration: ')
    except exceptions.InvalidSkyPilotConfigError as e:
        raise ValueError(str(e)) from e

    # Check for API server changes and validate them
    current_config = skypilot_config.to_dict()
    # If there is no changes to the config, we can return early
    if current_config == config:
        return config

    current_endpoint = current_config.get('api_server', {}).get('endpoint')
    new_endpoint = config.get('api_server', {}).get('endpoint')
    if current_endpoint != new_endpoint:
        raise ValueError('API server endpoint should not be changed to avoid '
                         'unexpected behavior.')

    # Check for workspace changes and validate them
    current_workspaces = current_config.get('workspaces', {})
    new_workspaces = config.get('workspaces', {})

    # Collect all workspaces that need to be checked for active resources
    workspaces_to_check = []
    workspaces_to_check_policy: Dict[str, Dict[str, List[str]]] = {
        'add': {},
        'update': {},
        'delete': {}
    }

    # Check each workspace that is being modified
    for workspace_name, new_workspace_config in new_workspaces.items():
        if workspace_name not in current_workspaces:
            users = workspaces_utils.get_workspace_users(new_workspace_config)
            workspaces_to_check_policy['add'][workspace_name] = users
            continue

        current_workspace_config = current_workspaces.get(workspace_name, {})

        # If workspace configuration is changing, validate and mark for checking
        if current_workspace_config != new_workspace_config:
            _validate_workspace_config(workspace_name, new_workspace_config)
            workspaces_to_check.append((workspace_name, 'update'))
            users = workspaces_utils.get_workspace_users(new_workspace_config)
            workspaces_to_check_policy['update'][workspace_name] = users

    # Check for workspace deletions
    for workspace_name in current_workspaces:
        if workspace_name not in new_workspaces:
            # Workspace is being deleted
            if workspace_name == constants.SKYPILOT_DEFAULT_WORKSPACE:
                raise ValueError(f'Cannot delete the default workspace '
                                 f'{constants.SKYPILOT_DEFAULT_WORKSPACE!r}.')
            workspaces_to_check.append((workspace_name, 'delete'))
            workspaces_to_check_policy['delete'][workspace_name] = ['*']

    # Check all workspaces for active resources in one efficient call
    _check_workspaces_have_no_active_resources(workspaces_to_check)

    # Use file locking to prevent race conditions
    lock_path = skypilot_config.get_skypilot_config_lock_path()
    try:
        with filelock.FileLock(lock_path,
                               _WORKSPACE_CONFIG_LOCK_TIMEOUT_SECONDS):
            # Convert to config_utils.Config and save
            config_obj = config_utils.Config.from_dict(config)
            skypilot_config.update_api_server_config_no_lock(config_obj)
            permission_service = permission.permission_service
            for operation, workspaces in workspaces_to_check_policy.items():
                for workspace_name, users in workspaces.items():
                    if operation == 'add':
                        permission_service.add_workspace_policy(
                            workspace_name, users)
                    elif operation == 'update':
                        permission_service.update_workspace_policy(
                            workspace_name, users)
                    elif operation == 'delete':
                        permission_service.remove_workspace_policy(
                            workspace_name)
    except filelock.Timeout as e:
        raise RuntimeError(
            f'Failed to update configuration due to a timeout '
            f'when trying to acquire the lock at {lock_path}. This may '
            'indicate another SkyPilot process is currently updating the '
            'configuration. Please try again or manually remove the lock '
            f'file if you believe it is stale.') from e

    # Validate the configuration by running sky check
    try:
        sky_check.check(quiet=True)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Configuration saved but '
                       f'validation check failed: {e}')
        # Don't fail the update if the check fails, just warn

    return config


def reject_request_for_unauthorized_workspace(user: models.User) -> None:
    """Rejects a request that has no permission to access active workspace.

    Args:
        user: The user making the request.

    Raises:
        PermissionDeniedError: If the user does not have permission to access
            the active workspace.
    """
    active_workspace = skypilot_config.get_active_workspace()
    if not permission.permission_service.check_workspace_permission(
            user.id, active_workspace):
        raise exceptions.PermissionDeniedError(
            f'User {user.name} ({user.id}) does not have '
            f'permission to access workspace {active_workspace!r}')


def is_workspace_private(workspace_config: Dict[str, Any]) -> bool:
    """Check if a workspace is private.

    Args:
        workspace_config: The workspace configuration dictionary.

    Returns:
        True if the workspace is private, False if it's public.
    """
    return workspace_config.get('private', False)


@annotations.lru_cache(scope='request', maxsize=1)
def workspaces_for_user(user_id: str) -> Dict[str, Any]:
    """Returns the workspaces that the user has access to.

    Args:
        user_id: The user id to check.

    Returns:
        A map from workspace name to workspace configuration.
    """
    workspaces = skypilot_config.get_nested(('workspaces',), default_value={})
    if constants.SKYPILOT_DEFAULT_WORKSPACE not in workspaces:
        workspaces[constants.SKYPILOT_DEFAULT_WORKSPACE] = {}
    user_workspaces = {}

    for workspace_name, workspace_config in workspaces.items():
        if permission.permission_service.check_workspace_permission(
                user_id, workspace_name):
            user_workspaces[workspace_name] = workspace_config
    return user_workspaces
