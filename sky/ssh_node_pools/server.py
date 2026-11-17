"""SSH Node Pool management API endpoints."""
import re
from typing import Any, Dict, List

import fastapi

from sky import skypilot_config
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import request_names
from sky.server.requests import requests as requests_lib
from sky.ssh_node_pools import core
from sky.utils import common_utils
from sky.workspaces import visibility

router = fastapi.APIRouter()


def _scope(request: fastapi.Request) -> visibility.InfraScope:
    # Unscoped for admins and for requests with no authenticated user.
    return visibility.scope_for(request.state.auth_user, need_slurm=False)


@router.get('')
def get_ssh_node_pools(request: fastapi.Request) -> Dict[str, Any]:
    """Get the SSH Node Pool configurations the caller may see.

    Admins get every pool as stored, including each host's sudo password and
    SSH key path. Any other caller gets only the pools their workspaces allow,
    with credential fields removed.
    """
    try:
        pools = core.get_all_pools()
    except Exception as e:
        raise fastapi.HTTPException(
            status_code=500,
            detail=
            f'Failed to get SSH Node Pools: {common_utils.format_exception(e)}')
    scope = _scope(request)
    if scope.unscoped:
        return pools
    assert scope.kubernetes is not None
    return core.redact_pool_secrets({
        name: config
        for name, config in pools.items()
        if f'ssh-{name}' in scope.kubernetes
    })


@router.post('')
def update_ssh_node_pools(pools_config: Dict[str, Any]) -> Dict[str, str]:
    """Update SSH Node Pool configurations."""
    try:
        core.update_pools(pools_config)
        return {'status': 'success'}
    except Exception as e:
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'Failed to update SSH Node Pools:'
                                    f' {common_utils.format_exception(e)}')


@router.delete('/{pool_name}')
def delete_ssh_node_pool(pool_name: str) -> Dict[str, str]:
    """Delete a SSH Node Pool configuration."""
    try:
        if core.delete_pool(pool_name):
            return {'status': 'success'}
        else:
            raise fastapi.HTTPException(
                status_code=404,
                detail=f'SSH Node Pool `{pool_name}` not found')
    except fastapi.HTTPException:
        raise
    except Exception as e:
        raise fastapi.HTTPException(status_code=500,
                                    detail='Failed to delete SSH Node Pool: '
                                    f'{common_utils.format_exception(e)}')


@router.post('/keys')
async def upload_ssh_key(request: fastapi.Request) -> Dict[str, str]:
    """Upload SSH private key."""
    try:
        form = await request.form()
        key_name = form.get('key_name')
        key_file = form.get('key_file')

        if not key_name or not key_file:
            raise fastapi.HTTPException(status_code=400,
                                        detail='Missing key_name or key_file')

        key_content = await key_file.read()
        key_path = core.upload_ssh_key(key_name, key_content.decode())

        return {'status': 'success', 'key_path': key_path}
    except fastapi.HTTPException:
        raise
    except Exception as e:
        raise fastapi.HTTPException(
            status_code=500,
            detail=
            f'Failed to upload SSH key: {common_utils.format_exception(e)}')


@router.get('/keys')
def list_ssh_keys() -> List[str]:
    """List available SSH keys."""
    try:
        return core.list_ssh_keys()
    except Exception as e:
        exception_msg = common_utils.format_exception(e)
        raise fastapi.HTTPException(
            status_code=500, detail=f'Failed to list SSH keys: {exception_msg}')


@router.post('/{pool_name}/deploy')
async def deploy_ssh_node_pool(request: fastapi.Request,
                               pool_name: str) -> Dict[str, str]:
    """Deploy SSH Node Pool using existing ssh_up functionality."""
    try:
        ssh_up_body = payloads.SSHUpBody(infra=pool_name, cleanup=False)
        await executor.schedule_request_async(
            request_id=request.state.request_id,
            request_name=request_names.RequestName.SSH_NODE_POOLS_UP,
            request_body=ssh_up_body,
            func=core.ssh_up,
            schedule_type=requests_lib.ScheduleType.LONG,
            auth_user=request.state.auth_user,
        )

        return {
            'status': 'success',
            'request_id': request.state.request_id,
            'message': f'SSH Node Pool `{pool_name}` deployment started'
        }
    except Exception as e:
        raise fastapi.HTTPException(status_code=500,
                                    detail=f'Failed to deploy SSH Node Pool: '
                                    f'{common_utils.format_exception(e)}')


@router.post('/deploy')
async def deploy_ssh_node_pool_general(
        request: fastapi.Request,
        ssh_up_body: payloads.SSHUpBody) -> Dict[str, str]:
    """Deploys all SSH Node Pools."""
    try:
        await executor.schedule_request_async(
            request_id=request.state.request_id,
            request_name=request_names.RequestName.SSH_NODE_POOLS_UP,
            request_body=ssh_up_body,
            func=core.ssh_up,
            schedule_type=requests_lib.ScheduleType.LONG,
            auth_user=request.state.auth_user,
        )

        pool_name = ssh_up_body.infra or 'default'
        return {
            'status': 'success',
            'request_id': request.state.request_id,
            'message': f'SSH Node Pool `{pool_name}` deployment started'
        }
    except Exception as e:
        raise fastapi.HTTPException(status_code=500,
                                    detail=f'Failed to deploy SSH Node Pool: '
                                    f'{common_utils.format_exception(e)}')


@router.post('/{pool_name}/down')
async def down_ssh_node_pool(request: fastapi.Request,
                             pool_name: str) -> Dict[str, str]:
    """Cleans up a SSH Node Pools."""
    try:
        ssh_up_body = payloads.SSHUpBody(infra=pool_name, cleanup=True)
        await executor.schedule_request_async(
            request_id=request.state.request_id,
            request_name=request_names.RequestName.SSH_NODE_POOLS_DOWN,
            request_body=ssh_up_body,
            func=core.ssh_up,  # Reuse ssh_up function with cleanup=True
            schedule_type=requests_lib.ScheduleType.LONG,
            auth_user=request.state.auth_user,
        )

        return {
            'status': 'success',
            'request_id': request.state.request_id,
            'message': f'SSH Node Pool `{pool_name}` teardown started'
        }
    except Exception as e:
        raise fastapi.HTTPException(
            status_code=500,
            detail=f'Failed to tear down SSH Node Pool: '
            f'{common_utils.format_exception(e)}')


@router.post('/down')
async def down_ssh_node_pool_general(
        request: fastapi.Request,
        ssh_up_body: payloads.SSHUpBody) -> Dict[str, str]:
    """Cleans up all SSH Node Pools."""
    try:
        # Set cleanup=True for down operation
        ssh_up_body.cleanup = True
        await executor.schedule_request_async(
            request_id=request.state.request_id,
            request_name=request_names.RequestName.SSH_NODE_POOLS_DOWN,
            request_body=ssh_up_body,
            func=core.ssh_up,  # Reuse ssh_up function with cleanup=True
            schedule_type=requests_lib.ScheduleType.LONG,
            auth_user=request.state.auth_user,
        )

        pool_name = ssh_up_body.infra or 'default'
        return {
            'status': 'success',
            'request_id': request.state.request_id,
            'message': f'SSH Node Pool `{pool_name}` teardown started'
        }
    except Exception as e:
        raise fastapi.HTTPException(
            status_code=500,
            detail=f'Failed to tear down SSH Node Pool: '
            f'{common_utils.format_exception(e)}')


@router.get('/{pool_name}/status')
def get_ssh_node_pool_status(request: fastapi.Request,
                             pool_name: str) -> Dict[str, str]:
    """Get the status of a specific SSH Node Pool."""
    context_name = f'ssh-{pool_name}'
    scope = _scope(request)
    if scope.kubernetes is not None and context_name not in scope.kubernetes:
        raise visibility.not_found('SSH Node Pool')
    # The check honors the active workspace's allowed_node_pools, so run it
    # under a workspace that allows the pool, not the server's default.
    workspace = visibility.workspace_allowing(
        skypilot_config.get_nested(('workspaces',), default_value={}) or {},
        'ssh', context_name)
    try:
        if workspace is None:
            is_ready, reason = core.ssh_status(context_name)
        else:
            with skypilot_config.local_active_workspace_ctx(workspace):
                is_ready, reason = core.ssh_status(context_name)

        # Strip ANSI escape codes from the reason text
        def strip_ansi_codes(text):
            if not text:
                return text
            # Remove ANSI escape sequences (color codes, formatting, etc.)
            text = re.sub(r'\x1b\[[0-9;]*m', '', text)
            # Remove 'disabled. Reason: ' prefix if present
            text = text.replace('disabled. Reason: ', '')
            return text

        cleaned_reason = strip_ansi_codes(reason) if reason else reason

        return {
            'pool_name': pool_name,
            'context_name': context_name,
            'status': 'Ready' if is_ready else 'Not Ready',
            'reason': cleaned_reason
        }
    except Exception as e:
        raise fastapi.HTTPException(
            status_code=500,
            detail=f'Failed to get SSH Node Pool status: '
            f'{common_utils.format_exception(e)}')
