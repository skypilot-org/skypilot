"""REST API for storage management."""

import contextlib
from typing import Iterator

import fastapi

from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import request_names
from sky.server.requests import requests as requests_lib
from sky.server.requests import role_filter
from sky.utils import registry
from sky.utils import volume as volume_utils
from sky.volumes import volume as volume_lib
from sky.volumes.server import core

logger = sky_logging.init_logger(__name__)

router = fastapi.APIRouter()


@router.get('')
async def volume_list(
    request: fastapi.Request,
    refresh: bool = fastapi.Depends(role_filter.force_viewer_volume_refresh),
) -> None:
    """Gets the volumes.

    Args:
        refresh: If True, refresh volume state from cloud APIs before returning.
            If False (default), return cached data from the database.
            For viewer-role callers this is forced to False by
            `role_filter.force_viewer_volume_refresh`.
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
        # Volume delete is a lightweight, bounded operation (e.g. a single
        # K8s PVC delete API call, capped by kubernetes.API_TIMEOUT). Use the
        # SHORT queue so it is not starved behind long-running LONG requests
        # like cluster launches.
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )


@contextlib.contextmanager
def _volume_errors_as_400() -> Iterator[None]:
    """Reports volume build/validation failures as a synchronous 400.

    Shared by /validate and /apply so the two cannot disagree on which volumes
    are legal, or on the error shape clients have to parse.
    """
    try:
        yield
    except fastapi.HTTPException:
        # Already a chosen HTTP response; do not re-wrap it as a volume error.
        raise
    except ValueError as e:
        # The common case: the volume really is malformed. No stack trace.
        logger.debug(f'Rejecting invalid volume: {e}')
        requests_lib.set_exception_stacktrace(e)
        raise fastapi.HTTPException(
            status_code=400, detail=exceptions.serialize_exception(e)) from e
    except Exception as e:
        # An internal fault lands here too, and would otherwise be reported as
        # the user's mistake with nothing in the server log.
        logger.exception(f'Unexpected error validating a volume: {e}')
        requests_lib.set_exception_stacktrace(e)
        raise fastapi.HTTPException(
            status_code=400, detail=exceptions.serialize_exception(e)) from e


@router.post('/validate')
async def volume_validate(
        _: fastapi.Request,
        volume_validate_body: payloads.VolumeValidateBody) -> None:
    """Validates a volume."""
    with _volume_errors_as_400():
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


@router.post('/apply')
async def volume_apply(request: fastapi.Request,
                       volume_apply_body: payloads.VolumeApplyBody) -> None:
    """Creates or registers a volume."""
    volume_cloud = volume_apply_body.cloud
    volume_type = volume_apply_body.volume_type
    volume_config = volume_apply_body.config
    if volume_config is None:
        volume_config = {}
    # Clients send explicit nulls for optional config fields (the dashboard
    # posts `namespace: null` when it is not set). validate_schema only drops
    # None at the top level, so drop them here or the schema rejects them.
    volume_config = {k: v for k, v in volume_config.items() if v is not None}
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
    # Validate here rather than trusting each client to call /validate first:
    # the dashboard posts straight to this endpoint.
    with _volume_errors_as_400():
        volume = volume_lib.Volume.from_components(
            name=volume_apply_body.name,
            type=volume_type,
            cloud=volume_cloud,
            region=volume_apply_body.region,
            zone=volume_apply_body.zone,
            size=volume_apply_body.size,
            labels=volume_apply_body.labels,
            use_existing=volume_apply_body.use_existing,
            # `use_existing` was folded into the config above for core; it is
            # not part of the volume config schema.
            config={
                k: v for k, v in volume_config.items() if k != 'use_existing'
            },
        )
        volume.validate()
    # Apply exactly what was validated. A size carrying a unit ('100Gi')
    # normalizes to a different number and the PVC spec appends 'Gi' to
    # whatever it is given; and when the client sends no config at all, the
    # dict holding the defaulted access mode is local to this handler, so
    # without this the worker gets None and cannot build a VolumeConfig.
    volume_apply_body.size = volume.size
    volume_apply_body.config = volume_config

    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.VOLUME_APPLY,
        request_body=volume_apply_body,
        func=core.volume_apply,
        # Volume apply is a lightweight, bounded operation (e.g. a few K8s PVC
        # read/create API calls, each capped by kubernetes.API_TIMEOUT). Use
        # the SHORT queue so it is not starved behind long-running LONG
        # requests like cluster launches.
        schedule_type=requests_lib.ScheduleType.SHORT,
        auth_user=request.state.auth_user,
    )
