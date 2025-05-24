"""REST API for workspace management."""

import fastapi

from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import requests as api_requests
from sky.workspaces import core

router = fastapi.APIRouter()


@router.get('')
async def get_workspace_config(request: fastapi.Request) -> None:
    """Gets workspace config on the server."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='workspaces',
        request_body=payloads.RequestBody(),
        func=core.get_workspaces,
        schedule_type=api_requests.ScheduleType.SHORT,
    )


@router.post('/update')
async def update_workspace(
        request: fastapi.Request,
        update_workspace_body: payloads.UpdateWorkspaceBody) -> None:
    """Updates a specific workspace configuration."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='update_workspace',
        request_body=update_workspace_body,
        func=core.update_workspace,
        schedule_type=api_requests.ScheduleType.SHORT,
    )


@router.post('/create')
async def create_workspace(
        request: fastapi.Request,
        create_workspace_body: payloads.CreateWorkspaceBody) -> None:
    """Creates a new workspace configuration."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='create_workspace',
        request_body=create_workspace_body,
        func=core.create_workspace,
        schedule_type=api_requests.ScheduleType.SHORT,
    )


@router.post('/delete')
async def delete_workspace(
        request: fastapi.Request,
        delete_workspace_body: payloads.DeleteWorkspaceBody) -> None:
    """Deletes a workspace configuration."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='delete_workspace',
        request_body=delete_workspace_body,
        func=core.delete_workspace,
        schedule_type=api_requests.ScheduleType.SHORT,
    )
