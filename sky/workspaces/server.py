"""REST API for workspace management."""

import fastapi

from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import request_names
from sky.server.requests import requests as api_requests
from sky.workspaces import core

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


@router.post('/config')
async def update_config(request: fastapi.Request,
                        update_config_body: payloads.UpdateConfigBody) -> None:
    """Updates the entire SkyPilot configuration."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.WORKSPACES_UPDATE_CONFIG,
        request_body=update_config_body,
        func=core.update_config,
        schedule_type=api_requests.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )
