"""Async SDK functions for managed jobs."""
import typing
from typing import Any, Dict, List, Optional, Tuple, Union

from sky import backends
from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.client import sdk_async
from sky.jobs.client import sdk
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import context_utils

if typing.TYPE_CHECKING:
    import io

    import requests

    import sky
else:
    requests = adaptors_common.LazyImport('requests')

logger = sky_logging.init_logger(__name__)


@usage_lib.entrypoint
async def launch(
    task: Union['sky.Task', 'sky.Dag'],
    name: Optional[str] = None,
    # Internal only:
    # pylint: disable=invalid-name
    _need_confirmation: bool = False,
    stream_logs: Optional[
        sdk_async.StreamConfig] = sdk_async.DEFAULT_STREAM_CONFIG,
) -> Tuple[Optional[int], Optional[backends.ResourceHandle]]:
    """Async version of launch() that launches a managed job."""
    request_id = await context_utils.to_thread(sdk.launch, task, name,
                                               _need_confirmation)
    if stream_logs is not None:
        return await sdk_async._stream_and_get(request_id, stream_logs)  # pylint: disable=protected-access
    else:
        return await sdk_async.get(request_id)


@usage_lib.entrypoint
async def queue(
    refresh: bool,
    skip_finished: bool = False,
    all_users: bool = False,
    stream_logs: Optional[
        sdk_async.StreamConfig] = sdk_async.DEFAULT_STREAM_CONFIG
) -> List[Dict[str, Any]]:
    """Async version of queue() that gets statuses of managed jobs."""
    request_id = await context_utils.to_thread(sdk.queue, refresh,
                                               skip_finished, all_users)
    if stream_logs is not None:
        return await sdk_async._stream_and_get(request_id, stream_logs)  # pylint: disable=protected-access
    else:
        return await sdk_async.get(request_id)


@usage_lib.entrypoint
async def cancel(
    name: Optional[str] = None,
    job_ids: Optional[List[int]] = None,
    all: bool = False,  # pylint: disable=redefined-builtin
    all_users: bool = False,
    stream_logs: Optional[
        sdk_async.StreamConfig] = sdk_async.DEFAULT_STREAM_CONFIG,
) -> None:
    """Async version of cancel() that cancels managed jobs."""
    request_id = await context_utils.to_thread(sdk.cancel, name, job_ids, all,
                                               all_users)
    if stream_logs is not None:
        return await sdk_async._stream_and_get(request_id, stream_logs)  # pylint: disable=protected-access
    else:
        return await sdk_async.get(request_id)


@usage_lib.entrypoint
async def tail_logs(cluster_name: str,
                    job_id: Optional[int],
                    follow: bool,
                    tail: int = 0,
                    output_stream: Optional['io.TextIOBase'] = None) -> int:
    """Async version of tail_logs() that tails the logs of a job."""
    return await context_utils.to_thread(
        sdk.tail_logs,
        cluster_name,
        job_id,
        follow,
        tail,
        output_stream,
    )


@usage_lib.entrypoint
async def download_logs(
        name: Optional[str],
        job_id: Optional[int],
        refresh: bool,
        controller: bool,
        local_dir: str = constants.SKY_LOGS_DIRECTORY) -> Dict[int, str]:
    """Async version of download_logs() that syncs down logs of managed jobs."""
    return await context_utils.to_thread(sdk.download_logs, name, job_id,
                                         refresh, controller, local_dir)


@usage_lib.entrypoint
async def dashboard() -> None:
    """Async version of dashboard() that starts a dashboard for managed jobs."""
    return await context_utils.to_thread(sdk.dashboard)


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
