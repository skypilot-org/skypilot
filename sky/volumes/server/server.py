"""REST API for storage management."""

import fastapi

from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import request_names
from sky.server.requests import requests as requests_lib
from sky.utils import registry
from sky.utils import volume as volume_utils
from sky.volumes.server import core

logger = sky_logging.init_logger(__name__)

router = fastapi.APIRouter()


@router.get('')
async def volume_list(request: fastapi.Request, refresh: bool = False) -> None:
    """Gets the volumes.

    Args:
        refresh: If True, refresh volume state from cloud APIs before returning.
            If False (default), return cached data from the database.
    """
    request_body = payloads.VolumeListBody(refresh=refresh)
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.VOLUME_LIST,
        request_body=request_body,
        func=core.volume_list,
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@router.post('/delete')
async def volume_delete(request: fastapi.Request,
                        volume_delete_body: payloads.VolumeDeleteBody) -> None:
    """Deletes a volume."""
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.VOLUME_DELETE,
        request_body=volume_delete_body,
        func=core.volume_delete,
        schedule_type=requests_lib.ScheduleType.LONG,
        auth_user=request.state.auth_user,
    )


@router.post('/validate')
async def volume_validate(
        _: fastapi.Request,
        volume_validate_body: payloads.VolumeValidateBody) -> None:
    """Validates a volume."""
    # pylint: disable=import-outside-toplevel
    from sky.volumes import volume as volume_lib

    try:
        volume_config = {
            'name': volume_validate_body.name,
            'type': volume_validate_body.volume_type,
            'infra': volume_validate_body.infra,
            'size': volume_validate_body.size,
            'labels': volume_validate_body.labels,
            'config': volume_validate_body.config,
            'use_existing': volume_validate_body.use_existing,
        }
        volume = volume_lib.Volume.from_yaml_config(volume_config)
        volume.validate()
    except Exception as e:
        requests_lib.set_exception_stacktrace(e)
        raise fastapi.HTTPException(status_code=400,
                                    detail=exceptions.serialize_exception(e))


@router.post('/apply')
async def volume_apply(request: fastapi.Request,
                       volume_apply_body: payloads.VolumeApplyBody) -> None:
    """Creates or registers a volume."""
    volume_cloud = volume_apply_body.cloud
    volume_type = volume_apply_body.volume_type
    volume_config = volume_apply_body.config
    if volume_config is None:
        volume_config = {}
    volume_config['use_existing'] = volume_apply_body.use_existing

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
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.VOLUME_APPLY,
        request_body=volume_apply_body,
        func=core.volume_apply,
        schedule_type=requests_lib.ScheduleType.LONG,
        auth_user=request.state.auth_user,
    )
