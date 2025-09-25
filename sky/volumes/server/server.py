"""REST API for storage management."""

import fastapi

from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import requests as requests_lib
from sky.utils import registry
from sky.utils import volume as volume_utils
from sky.volumes import volume as volume_lib
from sky.volumes.server import core

logger = sky_logging.init_logger(__name__)

router = fastapi.APIRouter()


@router.get('')
async def volume_list(request: fastapi.Request) -> None:
    """Gets the volumes."""
    auth_user = request.state.auth_user
    auth_user_env_vars_kwargs = {
        'env_vars': auth_user.to_env_vars()
    } if auth_user else {}
    volume_list_body = payloads.VolumeListBody(**auth_user_env_vars_kwargs)
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='volume_list',
        request_body=volume_list_body,
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


@router.post('/validate')
async def volume_validate(
        _: fastapi.Request,
        volume_apply_body: payloads.VolumeValidateBody) -> None:
    """Validates a volume."""
    try:
        volume_config = {
            'name': volume_apply_body.name,
            'type': volume_apply_body.volume_type,
            'infra': volume_apply_body.infra,
            'size': volume_apply_body.size,
            'labels': volume_apply_body.labels,
            'config': volume_apply_body.config,
            'resource_name': volume_apply_body.resource_name,
        }
        volume = volume_lib.Volume.from_yaml_config(volume_config)
        volume.validate()
    except Exception as e:
        raise fastapi.HTTPException(status_code=400,
                                    detail=exceptions.serialize_exception(e))


@router.post('/apply')
async def volume_apply(request: fastapi.Request,
                       volume_apply_body: payloads.VolumeApplyBody) -> None:
    """Creates or registers a volume."""
    volume_cloud = volume_apply_body.cloud
    volume_type = volume_apply_body.volume_type
    volume_config = volume_apply_body.config

    supported_volume_types = [
        volume_type.value for volume_type in volume_utils.VolumeType
    ]
    if volume_type not in supported_volume_types:
        raise fastapi.HTTPException(
            status_code=400, detail=f'Invalid volume type: {volume_type}')
    cloud = registry.CLOUD_REGISTRY.from_str(volume_cloud)
    if cloud is None:
        raise fastapi.HTTPException(status_code=400,
                                    detail=f'Invalid cloud: {volume_cloud}')
    if volume_type == volume_utils.VolumeType.PVC.value:
        if not cloud.is_same_cloud(clouds.Kubernetes()):
            raise fastapi.HTTPException(
                status_code=400,
                detail='PVC storage is only supported on Kubernetes')
        supported_access_modes = [
            access_mode.value for access_mode in volume_utils.VolumeAccessMode
        ]
        if volume_config is None:
            volume_config = {}
        access_mode = volume_config.get('access_mode')
        if access_mode is None:
            volume_config['access_mode'] = (
                volume_utils.VolumeAccessMode.READ_WRITE_ONCE.value)
        elif access_mode not in supported_access_modes:
            raise fastapi.HTTPException(
                status_code=400, detail=f'Invalid access mode: {access_mode}')
    elif volume_type == volume_utils.VolumeType.RUNPOD_NETWORK_VOLUME.value:
        if not cloud.is_same_cloud(clouds.RunPod()):
            raise fastapi.HTTPException(
                status_code=400,
                detail='Runpod network volume is only supported on Runpod')
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='volume_apply',
        request_body=volume_apply_body,
        func=core.volume_apply,
        schedule_type=requests_lib.ScheduleType.LONG,
    )
