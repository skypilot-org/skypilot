"""Workspace management core."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Tuple

import filelock

from sky import check as sky_check
from sky import exceptions
from sky import models
from sky import sky_logging
from sky import skypilot_config
from sky.backends import backend_utils
from sky.skylet import constants
from sky.usage import usage_lib
from sky.users import permission
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import locks
from sky.utils import resource_checker
from sky.utils import schemas
from sky.workspaces import utils as workspaces_utils

logger = sky_logging.init_logger(__name__)

# Lock for workspace configuration updates to prevent race conditions
_WORKSPACE_CONFIG_LOCK_TIMEOUT_SECONDS = 60


@dataclass
class WorkspaceConfigComparison:
    """Result of comparing current and new workspace configurations.

    This class encapsulates the results of analyzing differences between
    workspace configurations, particularly focusing on user access changes
    and their implications for resource validation.

    Attributes:
        only_user_access_changes: True if only allowed_users or private changed
        private_changed: True if private setting changed
        private_old: Old private setting value
        private_new: New private setting value
        allowed_users_changed: True if allowed_users changed
        allowed_users_old: Old allowed users list
        allowed_users_new: New allowed users list
        removed_users: Users removed from allowed_users
        added_users: Users added to allowed_users
    """
    only_user_access_changes: bool
    private_changed: bool
    private_old: bool
    private_new: bool
    allowed_users_changed: bool
    allowed_users_old: List[str]
    allowed_users_new: List[str]
    removed_users: List[str]
    added_users: List[str]


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


def _compare_workspace_configs(
    current_config: Dict[str, Any],
    new_config: Dict[str, Any],
) -> WorkspaceConfigComparison:
    """Compare current and new workspace configurations.

    Args:
        current_config: The current workspace configuration.
        new_config: The new workspace configuration.

    Returns:
        WorkspaceConfigComparison object containing the comparison results.
    """
    # Get private settings
    private_old = current_config.get('private', False)
    private_new = new_config.get('private', False)
    private_changed = private_old != private_new

    # Get allowed users (resolve to user IDs for comparison)
    allowed_users_old = workspaces_utils.get_workspace_users(
        current_config) if private_old else []
    allowed_users_new = workspaces_utils.get_workspace_users(
        new_config) if private_new else []

    # Convert to sets for easier comparison
    old_users_set = set(allowed_users_old)
    new_users_set = set(allowed_users_new)

    allowed_users_changed = old_users_set != new_users_set
    removed_users = list(old_users_set - new_users_set)
    added_users = list(new_users_set - old_users_set)

    # Check if only user access related fields changed
    # Create copies without the user access fields for comparison
    current_without_access = {
        k: v
        for k, v in current_config.items()
        if k not in ['private', 'allowed_users']
    }
    new_without_access = {
        k: v
        for k, v in new_config.items()
        if k not in ['private', 'allowed_users']
    }

    only_user_access_changes = current_without_access == new_without_access

    return WorkspaceConfigComparison(
        only_user_access_changes=only_user_access_changes,
        private_changed=private_changed,
        private_old=private_old,
        private_new=private_new,
        allowed_users_changed=allowed_users_changed,
        allowed_users_old=allowed_users_old,
        allowed_users_new=allowed_users_new,
        removed_users=removed_users,
        added_users=added_users)


def _validate_workspace_config_changes(workspace_name: str,
                                       current_config: Dict[str, Any],
                                       new_config: Dict[str, Any]) -> None:
    """Validate workspace configuration changes based on active resources.

    This function implements the logic:
    - If only allowed_users or private changed:
      - If private changed from true to false: allow it
      - If private changed from false to true: check that all active resources
        belong to allowed_users
      - If private didn't change: check that removed users don't have active
        resources
    - Otherwise: check that workspace has no active resources

    Args:
        workspace_name: The name of the workspace.
        current_config: The current workspace configuration.
        new_config: The new workspace configuration.

    Raises:
        ValueError: If the configuration change is not allowed due to active
        resources.
    """
    config_comparison = _compare_workspace_configs(current_config, new_config)

    if config_comparison.only_user_access_changes:
        # Only user access settings changed
        if config_comparison.private_changed:
            if (config_comparison.private_old and
                    not config_comparison.private_new):
                # Changed from private to public - always allow
                logger.info(
                    f'Workspace {workspace_name!r} changed from private to'
                    f' public.')
                return
            elif (not config_comparison.private_old and
                  config_comparison.private_new):
                # Changed from public to private - check that all active
                # resources belong to the new allowed users
                logger.info(
                    f'Workspace {workspace_name!r} changed from public to'
                    f' private. Checking that all active resources belong'
                    f' to allowed users.')

                error_summary, missed_users_names = (
                    resource_checker.check_users_workspaces_active_resources(
                        config_comparison.allowed_users_new, [workspace_name]))
                if error_summary:
                    error_msg=f'Cannot change workspace {workspace_name!r}' \
                    f' to private '
                    if missed_users_names:
                        missed_users_list = ', '.join(missed_users_names)
                        if len(missed_users_names) == 1:
                            error_msg += f'because the user ' \
                            f'{missed_users_list!r} has {error_summary}'
                        else:
                            error_msg += f'because the users ' \
                            f'{missed_users_list!r} have {error_summary}'
                        error_msg += ' but not in the allowed_users list.' \
                        ' Please either add the users to allowed_users or' \
                        ' ask them to terminate their resources.'
                    raise ValueError(error_msg)
        else:
            # Private setting didn't change, but allowed_users changed
            if (config_comparison.allowed_users_changed and
                    config_comparison.removed_users):
                # Check that removed users don't have active resources
                logger.info(
                    f'Checking that removed users'
                    f' {config_comparison.removed_users} do not have'
                    f' active resources in workspace {workspace_name!r}.')
                user_operations = []
                for user_id in config_comparison.removed_users:
                    user_operations.append((user_id, 'remove'))
                resource_checker.check_no_active_resources_for_users(
                    user_operations)
    else:
        # Other configuration changes - check that workspace has no active
        # resources
        logger.info(
            f'Non-user-access configuration changes detected for'
            f' workspace {workspace_name!r}. Checking that workspace has'
            f' no active resources.')
        resource_checker.check_no_active_resources_for_workspaces([
            (workspace_name, 'update')
        ])


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
            active clusters or managed jobs that prevent the configuration
            change.
            The validation logic depends on what changed:
            - If only allowed_users or private changed:
              - Private true->false: Always allowed
              - Private false->true: All active resources must belong to
                allowed_users
              - allowed_users changes: Removed users must not have active
                resources
            - Other changes: Workspace must have no active resources
        FileNotFoundError: If the config file cannot be found.
        PermissionError: If the config file cannot be written.
    """
    _validate_workspace_config(workspace_name, config)

    # Get the current workspace configuration for comparison
    current_workspaces = skypilot_config.get_nested(('workspaces',),
                                                    default_value={})
    current_config = current_workspaces.get(workspace_name, {})

    if current_config:
        lock_id = backend_utils.workspace_lock_id(workspace_name)
        lock_timeout = backend_utils.WORKSPACE_LOCK_TIMEOUT_SECONDS
        try:
            with locks.get_lock(lock_id, lock_timeout):
                # Validate the configuration changes based on active resources
                _validate_workspace_config_changes(workspace_name,
                                                   current_config, config)
        except locks.LockTimeout as e:
            raise RuntimeError(
                f'Failed to validate workspace {workspace_name!r} due to '
                'a timeout when trying to access database. Please '
                f'try again or manually remove the lock at {lock_id}. '
                f'{common_utils.format_exception(e)}') from None

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
    resource_checker.check_no_active_resources_for_workspaces([(workspace_name,
                                                                'delete')])

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
    workspaces_to_check: List[Tuple[str, str]] = []
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
    resource_checker.check_no_active_resources_for_workspaces(
        workspaces_to_check)

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
