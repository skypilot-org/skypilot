"""REST API for storage management."""

import contextlib
import hashlib
import os
from typing import Any, Dict, Generator, List

import fastapi
import filelock
from passlib.hash import apr_md5_crypt

import sky
from sky import global_user_state
from sky import models
from sky import sky_logging
from sky.server.requests import payloads
from sky.skylet import constants
from sky.users import permission
from sky.users import rbac
from sky.utils import common
from sky.utils import common_utils
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import requests as api_requests
from sky import global_user_state
from sky import provision
from sky import clouds
from sky.storages import core, storage
from sky.server.requests import requests as requests_lib

logger = sky_logging.init_logger(__name__)



router = fastapi.APIRouter()

@router.get('/ls')
async def storage_ls(request: fastapi.Request) -> None:
    """Gets the storages."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='storage_ls',
        request_body=payloads.RequestBody(),
        func=core.storage_ls,
        schedule_type=requests_lib.ScheduleType.SHORT,
    )


@router.post('/delete')
async def storage_delete(request: fastapi.Request,
                         storage_body: payloads.StorageBody) -> None:
    """Deletes a storage."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='storage_delete',
        request_body=storage_body,
        func=core.storage_delete,
        schedule_type=requests_lib.ScheduleType.LONG,
    )

@router.post('/create')
async def storage_create(request: fastapi.Request, storage_create_body: payloads.StorageCreateBody) -> None:
    """Creates a storage."""
    storage_name = storage_create_body.name
    storage_cloud = storage_create_body.cloud
    storage_region = storage_create_body.region
    storage_zone = storage_create_body.zone
    storage_type = storage_create_body.type
    storage_size = storage_create_body.size
    storage_spec = storage_create_body.spec

    supported_storage_types = [storage_type.value for storage_type in storage.StorageType]
    if storage_type not in supported_storage_types:
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'Invalid storage type: {storage_type}')
    cloud=sky.CLOUD_REGISTRY.from_str(storage_cloud)
    if storage_type == storage.StorageType.PVC.value:
        if not cloud.is_same_cloud(clouds.Kubernetes()):
            raise fastapi.HTTPException(status_code=400,
                                        detail='PVC storage is only supported on Kubernetes')
        supported_access_modes = [access_mode.value for access_mode in storage.StorageAccessMode]
        access_mode=storage_spec.get('access_mode')
        if access_mode is None:
            storage_spec['access_mode']=storage.StorageAccessMode.READ_WRITE_ONCE.value
        elif access_mode not in supported_access_modes:
            raise fastapi.HTTPException(status_code=400,
                                        detail=f'Invalid access mode: {access_mode}')
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='storage_create',
        request_body=storage_create_body,
        func=core.storage_create,
        schedule_type=requests_lib.ScheduleType.LONG,
    )



