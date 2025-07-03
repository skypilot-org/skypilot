"""REST API for storage management."""

import fastapi

import sky
from sky import clouds
from sky import sky_logging
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import requests as requests_lib
from sky.volumes import volume
from sky.volumes.server import core

logger = sky_logging.init_logger(__name__)

router = fastapi.APIRouter()


@router.get('')
async def volume_list(request: fastapi.Request) -> None:
    """Gets the volumes."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='volume_list',
        request_body=payloads.RequestBody(),
        func=core.volume_list,
        schedule_type=requests_lib.ScheduleType.SHORT,
    )


@router.post('/delete')
async def volume_delete(request: fastapi.Request,
                        volume_delete_body: payloads.VolumeDeleteBody) -> None:
    """Deletes a volume."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='volume_delete',
        request_body=volume_delete_body,
        func=core.volume_delete,
        schedule_type=requests_lib.ScheduleType.LONG,
    )


@router.post('/apply')
async def volume_apply(request: fastapi.Request,
                       volume_apply_body: payloads.VolumeApplyBody) -> None:
    """Creates or registers a volume."""
    volume_cloud = volume_apply_body.cloud
    volume_type = volume_apply_body.volume_type
    volume_config = volume_apply_body.config

    supported_volume_types = [
        volume_type.value for volume_type in volume.VolumeType
    ]
    if volume_type not in supported_volume_types:
        raise fastapi.HTTPException(
            status_code=400, detail=f'Invalid volume type: {volume_type}')
    cloud = sky.CLOUD_REGISTRY.from_str(volume_cloud)
    if cloud is None:
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'Invalid cloud: {volume_cloud}')
    if volume_type == volume.VolumeType.PVC.value:
        if not cloud.is_same_cloud(clouds.Kubernetes()):
            raise fastapi.HTTPException(
                status_code=400,
                detail='PVC storage is only supported on Kubernetes')
        supported_access_modes = [
            access_mode.value for access_mode in volume.VolumeAccessMode
        ]
        if volume_config is None:
            volume_config = {}
        access_mode = volume_config.get('access_mode')
        if access_mode is None:
            volume_config[
                'access_mode'] = volume.VolumeAccessMode.READ_WRITE_ONCE.value
        elif access_mode not in supported_access_modes:
            raise fastapi.HTTPException(
                status_code=400, detail=f'Invalid access mode: {access_mode}')
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='volume_apply',
        request_body=volume_apply_body,
        func=core.volume_apply,
        schedule_type=requests_lib.ScheduleType.LONG,
    )
