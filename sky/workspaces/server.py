"""REST API for workspace management."""

from typing import Optional
import uuid

import fastapi

from sky import check as sky_check
from sky import models
from sky import sky_logging
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import preconditions
from sky.server.requests import request_names
from sky.server.requests import requests as api_requests
from sky.utils import common_utils
from sky.workspaces import core

logger = sky_logging.init_logger(__name__)

router = fastapi.APIRouter()


@router.get('')
# pylint: disable=redefined-builtin
async def get(request: fastapi.Request) -> None:
    """Gets workspace config on the server."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.WORKSPACES_GET,
        request_body=payloads.RequestBody(),
        func=core.get_workspaces,
        schedule_type=api_requests.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@router.post('/update')
async def update(request: fastapi.Request,
                 update_workspace_body: payloads.UpdateWorkspaceBody) -> None:
    """Updates a specific workspace configuration."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.WORKSPACES_UPDATE,
        request_body=update_workspace_body,
        func=core.update_workspace,
        schedule_type=api_requests.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@router.post('/create')
async def create(request: fastapi.Request,
                 create_workspace_body: payloads.CreateWorkspaceBody) -> None:
    """Creates a new workspace configuration."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.WORKSPACES_CREATE,
        request_body=create_workspace_body,
        func=core.create_workspace,
        schedule_type=api_requests.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@router.post('/delete')
async def delete(request: fastapi.Request,
                 delete_workspace_body: payloads.DeleteWorkspaceBody) -> None:
    """Deletes a workspace configuration."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.WORKSPACES_DELETE,
        request_body=delete_workspace_body,
        func=core.delete_workspace,
        schedule_type=api_requests.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@router.post('/batch_add_users')
async def batch_add_users(request: fastapi.Request,
                          body: payloads.WorkspaceBatchAddUsersBody) -> None:
    """Adds users to ``allowed_users`` of multiple private workspaces."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.WORKSPACES_BATCH_ADD_USERS,
        request_body=body,
        func=core.batch_add_users_to_workspaces,
        schedule_type=api_requests.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@router.post('/batch_remove_users')
async def batch_remove_users(
        request: fastapi.Request,
        body: payloads.WorkspaceBatchRemoveUsersBody) -> None:
    """Removes users from ``allowed_users`` of multiple private workspaces."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.WORKSPACES_BATCH_REMOVE_USERS,
        request_body=body,
        func=core.batch_remove_users_from_workspaces,
        schedule_type=api_requests.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@router.get('/config')
async def get_config(request: fastapi.Request) -> None:
    """Gets the entire SkyPilot configuration."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.WORKSPACES_GET_CONFIG,
        request_body=payloads.GetConfigBody(),
        func=core.get_config,
        schedule_type=api_requests.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


async def schedule_update_config(
    request_id: str,
    update_config_body: payloads.UpdateConfigBody,
    auth_user: Optional[models.User],
    request_name: request_names.RequestName = (
        request_names.RequestName.WORKSPACES_UPDATE_CONFIG),
) -> None:
    """Schedules a config save and the `sky check` that follows it.

    Two requests are scheduled. The first runs `core.update_config` under
    `request_id` and is the request the caller polls. The second is a
    `sky check` with its own id, gated on the first request SUCCEEDING. The
    check refreshes the enabled-clouds cache and the per-context check
    results that the infra page and the optimizer read. Running it as a
    separate request lets the save return as soon as the write lands
    instead of waiting for every cloud to be probed.

    Callers outside this module that schedule `core.update_config` as a
    request (for example a config rollback) should call this instead of
    `executor.schedule_request_async` directly, so they keep the follow-up
    check.

    Args:
        request_id: The id of the save request.
        update_config_body: The save request body.
        auth_user: The authenticated user, if any. Passed to both requests.
        request_name: The request name for the save. Defaults to the name
            the dashboard's config editor uses.

    Raises:
        Whatever `executor.schedule_request_async` raises for the save
        request. A failure scheduling the follow-up check is logged and
        not raised, because the save is already queued by then.
    """
    await executor.schedule_request_async(
        request_id=request_id,
        request_name=request_name,
        request_body=update_config_body,
        func=core.update_config,
        schedule_type=api_requests.ScheduleType.SHORT,
        auth_user=auth_user,
    )
    check_request_id = str(uuid.uuid4())
    try:
        await executor.schedule_request_async(
            request_id=check_request_id,
            request_name=request_names.RequestName.CHECK,
            # Copy env_vars so the check runs as the same user as the save
            # when auth_user is not set (prepare_request_async falls back to
            # the env_vars user id).
            request_body=payloads.CheckBody(
                env_vars=dict(update_config_body.env_vars)),
            func=sky_check.check,
            schedule_type=api_requests.ScheduleType.SHORT,
            precondition=preconditions.RequestSucceededPrecondition(
                request_id=check_request_id, awaited_request_id=request_id),
            auth_user=auth_user,
        )
    except Exception as e:  # pylint: disable=broad-except
        # The save is already queued and will commit. Failing here would
        # tell the client the save failed when it did not. A missing refresh
        # only leaves the enabled-clouds cache stale until the next
        # `sky check`, which is the same outcome the inline check had when
        # it failed (it logged a warning and the save succeeded).
        logger.warning(
            f'Config save {request_id} was queued but the follow-up sky '
            f'check could not be scheduled: '
            f'{common_utils.format_exception(e)}. Run `sky check` to '
            'refresh enabled infra.')


@router.post('/config')
async def update_config(request: fastapi.Request,
                        update_config_body: payloads.UpdateConfigBody) -> None:
    """Updates the entire SkyPilot configuration.

    See `schedule_update_config` for why this schedules two requests.
    """
    await schedule_update_config(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.WORKSPACES_UPDATE_CONFIG,
        update_config_body=update_config_body,
        auth_user=request.state.auth_user,
    )
