"""SDK functions for managed jobs."""
import json
import typing
from typing import Any, Dict

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.server import common as server_common
from sky.server.requests import payloads
from sky.usage import usage_lib
from sky.utils import annotations
from sky.utils import context

if typing.TYPE_CHECKING:
    import requests
else:
    requests = adaptors_common.LazyImport('requests')

logger = sky_logging.init_logger(__name__)


@context.contextual
@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def apply(volume_config: Dict[str, Any]) -> server_common.RequestId:
    """Creates or registers a volume.
    """
    body = payloads.VolumeApplyBody(name=volume_config['name'],
                                    type=volume_config['type'],
                                    cloud=volume_config['cloud'],
                                    region=volume_config['region'],
                                    zone=volume_config['zone'],
                                    spec=volume_config['spec'])
    response = requests.post(f'{server_common.get_server_url()}/volumes/apply',
                             json=json.loads(body.model_dump_json()),
                             cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)
