"""SDK functions for managed jobs."""
import json
import typing
from typing import Any, Dict, List

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.server import common as server_common
from sky.server.requests import payloads
from sky.usage import usage_lib
from sky.utils import annotations
from sky.utils import context
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

    Args:
        volume: The volume to apply.

    Returns:
        The request ID of the apply request.
    """
    body = payloads.VolumeApplyBody(name=volume.name,
                                    volume_type=volume.type,
                                    cloud=volume.cloud,
                                    region=volume.region,
                                    zone=volume.zone,
                                    size=volume.size,
                                    config=volume.config)
    response = server_common.make_authenticated_request(
        'POST',
        '/volumes/apply',
        json=json.loads(body.model_dump_json()),
    )
    return server_common.get_request_id(response)


@context.contextual
@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def ls() -> server_common.RequestId[List[Dict[str, Any]]]:
    """Lists all volumes.

    Returns:
        The request ID of the list request.
    """
    response = server_common.make_authenticated_request(
        'GET',
        '/volumes',
    )
    return server_common.get_request_id(response)


@context.contextual
@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def delete(names: List[str]) -> server_common.RequestId[None]:
    """Deletes volumes.

    Args:
        names: List of volume names to delete.

    Returns:
        The request ID of the delete request.
    """
    body = payloads.VolumeDeleteBody(names=names)
    response = server_common.make_authenticated_request(
        'POST',
        '/volumes/delete',
        json=json.loads(body.model_dump_json()),
    )
    return server_common.get_request_id(response)
