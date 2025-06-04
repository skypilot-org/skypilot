"""Async SDK functions for managed jobs."""
import json
import typing
from typing import Dict, List, Optional, Union
import webbrowser

import click

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.client import common as client_common
from sky.client import sdk
from sky.client import sdk_async
from sky.server import common as server_common
from sky.server.requests import payloads
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import dag_utils

if typing.TYPE_CHECKING:
    import io

    import requests

    import sky
else:
    requests = adaptors_common.LazyImport('requests')

logger = sky_logging.init_logger(__name__)

@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
async def launch(
    task: Union['sky.Task', 'sky.Dag'],
    name: Optional[str] = None,
    # Internal only:
    # pylint: disable=invalid-name
    _need_confirmation: bool = False,
) -> Any:
    """Async version of launch() that launches a managed job."""
    request_id = sdk.launch(task, name, _need_confirmation)
    return await sdk_async.get(request_id)

@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
async def queue(refresh: bool,
          skip_finished: bool = False,
          all_users: bool = False) -> Any:
    """Async version of queue() that gets statuses of managed jobs."""
    request_id = sdk.queue(refresh, skip_finished, all_users)
    return await sdk_async.get(request_id)

@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
async def cancel(
    name: Optional[str] = None,
    job_ids: Optional[List[int]] = None,
    all: bool = False,  # pylint: disable=redefined-builtin
    all_users: bool = False,
) -> Any:
    """Async version of cancel() that cancels managed jobs."""
    request_id = sdk.cancel(name, job_ids, all, all_users)
    return await sdk_async.get(request_id)

@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
async def tail_logs(name: Optional[str] = None,
              job_id: Optional[int] = None,
              follow: bool = True,
              controller: bool = False,
              refresh: bool = False,
              tail: Optional[int] = None,
              output_stream: Optional['io.TextIOBase'] = None) -> Any:
    """Async version of tail_logs() that tails logs of managed jobs."""
    request_id = sdk.tail_logs(name, job_id, follow, controller, refresh, tail, output_stream)
    return await sdk_async.get(request_id)

@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
async def download_logs(
        name: Optional[str],
        job_id: Optional[int],
        refresh: bool,
        controller: bool,
        local_dir: str = constants.SKY_LOGS_DIRECTORY) -> Any:
    """Async version of download_logs() that syncs down logs of managed jobs."""
    request_id = sdk.download_logs(name, job_id, refresh, controller, local_dir)
    return await sdk_async.get(request_id)

@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
async def dashboard() -> None:
    """Async version of dashboard() that starts a dashboard for managed jobs."""
    return sdk.dashboard()

# Deprecated functions
spot_launch = common_utils.deprecated_function(
    launch,
    name='sky.jobs.launch',
    deprecated_name='spot_launch',
    removing_version='0.8.0',
    override_argument={'use_spot': True})
spot_queue = common_utils.deprecated_function(queue,
                                              name='sky.jobs.queue',
                                              deprecated_name='spot_queue',
                                              removing_version='0.8.0')
spot_cancel = common_utils.deprecated_function(cancel,
                                               name='sky.jobs.cancel',
                                               deprecated_name='spot_cancel',
                                               removing_version='0.8.0')
spot_tail_logs = common_utils.deprecated_function(
    tail_logs,
    name='sky.jobs.tail_logs',
    deprecated_name='spot_tail_logs',
    removing_version='0.8.0')
