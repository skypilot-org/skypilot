"""SSH Node Pool management API endpoints."""
from typing import Dict, Any, List

import fastapi

from sky.ssh_node_pools import core
from sky.utils import common_utils

router = fastapi.APIRouter()


@router.get('')
async def get_ssh_node_pools() -> Dict[str, Any]:
    """Get all SSH Node Pool configurations."""
    try:
        return core.get_all_pools()
    except Exception as e:
        raise fastapi.HTTPException(
            status_code=500, 
            detail=f"Failed to get SSH Node Pools: {common_utils.format_exception(e)}"
        )


@router.post('')
async def update_ssh_node_pools(pools_config: Dict[str, Any]) -> Dict[str, str]:
    """Update SSH Node Pool configurations."""
    try:
        core.update_pools(pools_config)
        return {"status": "success"}
    except Exception as e:
        raise fastapi.HTTPException(
            status_code=400,
            detail=f"Failed to update SSH Node Pools: {common_utils.format_exception(e)}"
        )


@router.delete('/{pool_name}')
async def delete_ssh_node_pool(pool_name: str) -> Dict[str, str]:
    """Delete a SSH Node Pool configuration."""
    try:
        if core.delete_pool(pool_name):
            return {"status": "success"}
        else:
            raise fastapi.HTTPException(
                status_code=404, 
                detail=f"SSH Node Pool '{pool_name}' not found"
            )
    except fastapi.HTTPException:
        raise
    except Exception as e:
        raise fastapi.HTTPException(
            status_code=500,
            detail=f"Failed to delete SSH Node Pool: {common_utils.format_exception(e)}"
        )


@router.post('/keys')
async def upload_ssh_key(request: fastapi.Request) -> Dict[str, str]:
    """Upload SSH private key."""
    try:
        form = await request.form()
        key_name = form.get("key_name")
        key_file = form.get("key_file")
        
        if not key_name or not key_file:
            raise fastapi.HTTPException(
                status_code=400, 
                detail="Missing key_name or key_file"
            )
        
        key_content = await key_file.read()
        key_path = core.upload_ssh_key(key_name, key_content.decode())
        
        return {"status": "success", "key_path": key_path}
    except fastapi.HTTPException:
        raise
    except Exception as e:
        raise fastapi.HTTPException(
            status_code=500,
            detail=f"Failed to upload SSH key: {common_utils.format_exception(e)}"
        )


@router.get('/keys')
async def list_ssh_keys() -> List[str]:
    """List available SSH keys."""
    try:
        return core.list_ssh_keys()
    except Exception as e:
        raise fastapi.HTTPException(
            status_code=500,
            detail=f"Failed to list SSH keys: {common_utils.format_exception(e)}"
        )


@router.post('/{pool_name}/deploy')
async def deploy_ssh_node_pool(request: fastapi.Request, pool_name: str) -> Dict[str, str]:
    """Deploy SSH Node Pool using existing ssh_up functionality."""
    try:
        # Import here to avoid circular dependencies
        from sky.server.requests import payloads, executor, requests as requests_lib
        from sky import core as sky_core
        
        ssh_up_body = payloads.SSHUpBody(infra=pool_name, cleanup=False)
        executor.schedule_request(
            request_id=request.state.request_id,
            request_name='ssh_up',
            request_body=ssh_up_body,
            func=sky_core.ssh_up,
            schedule_type=requests_lib.ScheduleType.LONG,
        )
        
        return {"status": "success", "message": f"SSH Node Pool '{pool_name}' deployment started"}
    except Exception as e:
        raise fastapi.HTTPException(
            status_code=500,
            detail=f"Failed to deploy SSH Node Pool: {common_utils.format_exception(e)}"
        )


@router.get('/{pool_name}/status')
async def get_ssh_node_pool_status(pool_name: str) -> Dict[str, str]:
    """Get the status of a specific SSH Node Pool."""
    try:
        from sky import core as sky_core
        
        # Call ssh_status to check the context
        context_name = f"ssh-{pool_name}"
        is_ready, reason = sky_core.ssh_status(context_name)
        
        return {
            "pool_name": pool_name,
            "context_name": context_name,
            "status": "Ready" if is_ready else "Not Ready",
            "reason": reason
        }
    except Exception as e:
        raise fastapi.HTTPException(
            status_code=500,
            detail=f"Failed to get SSH Node Pool status: {common_utils.format_exception(e)}"
        ) 