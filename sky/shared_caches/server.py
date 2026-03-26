"""REST API for shared cache management."""

import fastapi

from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import request_names
from sky.server.requests import requests as requests_lib
from sky.shared_caches import core

router = fastapi.APIRouter()


@router.get('')
async def shared_caches_list(request: fastapi.Request) -> None:
    """Lists all shared cache entries."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.SHARED_CACHES_LIST,
        request_body=payloads.RequestBody(),
        func=core.list_shared_caches,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@router.post('/upsert')
async def shared_caches_upsert(request: fastapi.Request,
                               body: payloads.SharedCacheUpsertBody) -> None:
    """Adds or updates a shared cache entry."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.SHARED_CACHES_UPSERT,
        request_body=body,
        func=core.upsert_shared_cache,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@router.post('/delete')
async def shared_caches_delete(request: fastapi.Request,
                               body: payloads.SharedCacheDeleteBody) -> None:
    """Deletes a shared cache entry."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.SHARED_CACHES_DELETE,
        request_body=body,
        func=core.delete_shared_cache,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@router.post('/storage_classes')
async def shared_caches_storage_classes(
        request: fastapi.Request,
        body: payloads.SharedCacheStorageClassesBody) -> None:
    """Lists storage classes for a Kubernetes context."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.SHARED_CACHES_STORAGE_CLASSES,
        request_body=body,
        func=core.list_storage_classes,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@router.get('/rwm_volumes')
async def shared_caches_rwm_volumes(request: fastapi.Request) -> None:
    """Lists existing SkyPilot volumes with ReadWriteMany access mode."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.SHARED_CACHES_RWM_VOLUMES,
        request_body=payloads.RequestBody(),
        func=core.list_rwm_volumes,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )
