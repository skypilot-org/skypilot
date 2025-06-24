"""SDK functions for managed jobs."""
import json
import typing
from typing import List

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
def apply(volume: volume_lib.Volume) -> server_common.RequestId:
    """Creates or registers a volume.
    """
    body = payloads.VolumeApplyBody(name=volume.name,
                                    volume_type=volume.type,
                                    cloud=volume.cloud,
                                    region=volume.region,
                                    zone=volume.zone,
                                    size=volume.size,
                                    config=volume.config)
    response = requests.post(f'{server_common.get_server_url()}/volumes/apply',
                             json=json.loads(body.model_dump_json()),
                             cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


@context.contextual
@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def ls() -> server_common.RequestId:
    """Lists all volumes."""
    response = requests.get(f'{server_common.get_server_url()}/volumes',
                            cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


@context.contextual
@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def delete(names: List[str]) -> server_common.RequestId:
    """Deletes a volume."""
    body = payloads.VolumeDeleteBody(names=names)
    response = requests.post(f'{server_common.get_server_url()}/volumes/delete',
                             json=json.loads(body.model_dump_json()),
                             cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)
