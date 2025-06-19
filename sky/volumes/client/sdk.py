"""SDK functions for managed jobs."""
import json
import typing
from typing import Any, Dict, List, Optional, Union
import webbrowser

import click

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.client import common as client_common
from sky.client import sdk
from sky.server import common as server_common
from sky.server.requests import payloads
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import admin_policy_utils
from sky.utils import common_utils
from sky.utils import context
from sky.utils import dag_utils
from sky.utils import annotations


if typing.TYPE_CHECKING:
    import io

    import requests

    import sky
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
    body = payloads.StorageCreateBody(
        name=volume_config['name'],
        type=volume_config['type'],
        size=volume_config['size'],
        cloud=volume_config['cloud'],
        region=volume_config['region'],
        zone=volume_config['zone'],
        spec=volume_config['spec'])
    response = requests.post(f'{server_common.get_server_url()}/volumes/create',
                             json=json.loads(body.model_dump_json()),
                             cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)

