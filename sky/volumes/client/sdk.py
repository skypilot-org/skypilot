"""SDK functions for volumes."""
import json
import typing
from typing import List

from sky import exceptions
from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.schemas.api import responses
from sky.server import common as server_common
from sky.server import versions
from sky.server.requests import payloads
from sky.usage import usage_lib
from sky.utils import annotations
from sky.utils import context
from sky.utils import ux_utils
from sky.volumes import volume as volume_lib

if typing.TYPE_CHECKING:
    import requests
else:
    requests = adaptors_common.LazyImport('requests')

logger = sky_logging.init_logger(__name__)


@context.contextual
@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def apply(volume: volume_lib.Volume) -> server_common.RequestId[None]:
    """Creates or registers a volume.

    Example:
        .. code-block:: python

            import sky.volumes
            cfg = {
                'name': 'pvc',
                'type': 'k8s-pvc',
                'size': '100GB',
                'labels': {
                    'key': 'value',
                },
            }
            vol = sky.volumes.Volume.from_yaml_config(cfg)
            request_id = sky.volumes.apply(vol)
            sky.get(request_id)

            or

            import sky.volumes
            vol = sky.volumes.Volume(
                name='vol',
                type='runpod-network-volume',
                infra='runpod/ca/CA-MTL-1',
                size='100GB',
            )
            request_id = sky.volumes.apply(vol)
            sky.get(request_id)

    Args:
        volume: The volume to apply.

    Returns:
        The request ID of the apply request.
    """
    body = payloads.VolumeApplyBody(
        name=volume.name,
        volume_type=volume.type,
        cloud=volume.cloud,
        region=volume.region,
        zone=volume.zone,
        size=volume.size,
        config=volume.config,
        labels=volume.labels,
        use_existing=volume.use_existing,
    )
    response = server_common.make_authenticated_request(
        'POST', '/volumes/apply', json=json.loads(body.model_dump_json()))
    return server_common.get_request_id(response)


@context.contextual
@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
@versions.minimal_api_version(20)
def validate(volume: volume_lib.Volume) -> None:
    """Validates the volume.

    All validation is done on the server side.

    Args:
        volume: The volume to validate.

    Raises:
        ValueError: If the volume is invalid.
    """
    body = payloads.VolumeValidateBody(
        name=volume.name,
        volume_type=volume.type,
        infra=volume.infra,
        size=volume.size,
        config=volume.config,
        labels=volume.labels,
        use_existing=volume.use_existing,
    )
    response = server_common.make_authenticated_request(
        'POST', '/volumes/validate', json=json.loads(body.model_dump_json()))
    if response.status_code == 400:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.deserialize_exception(
                response.json().get('detail'))


@context.contextual
@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def ls(
    refresh: bool = False
) -> server_common.RequestId[List[responses.VolumeRecord]]:
    """Lists all volumes.

    Args:
        refresh: If True, refresh volume state from cloud APIs before returning.
            This makes the call slower but returns the most up-to-date data.
            If False (default), return cached data from the database which is
            updated periodically by the background daemon.

    Returns:
        The request ID of the list request.
    """
    params = {}
    if refresh:
        params['refresh'] = 'true'
    response = server_common.make_authenticated_request(
        'GET',
        '/volumes',
        params=params,
    )
    return server_common.get_request_id(response)


@context.contextual
@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def delete(names: List[str],
           purge: bool = False) -> server_common.RequestId[None]:
    """Deletes volumes.

    Args:
        names: List of volume names to delete.
        purge: If True, delete the volume from the database even if the
          deletion API fails.

    Returns:
        The request ID of the delete request.
    """
    body = payloads.VolumeDeleteBody(names=names, purge=purge)
    response = server_common.make_authenticated_request(
        'POST', '/volumes/delete', json=json.loads(body.model_dump_json()))
    return server_common.get_request_id(response)
