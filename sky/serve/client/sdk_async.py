"""Async SDK for SkyServe."""
import asyncio
import typing
from typing import Any, Dict, List, Optional, Tuple, Union

from sky.client import sdk_async
from sky.serve.client import sdk
from sky.usage import usage_lib

if typing.TYPE_CHECKING:
    import io

    import sky
    from sky.serve import serve_utils


@usage_lib.entrypoint
async def up(
    task: Union['sky.Task', 'sky.Dag'],
    service_name: str,
    # Internal only:
    # pylint: disable=invalid-name
    _need_confirmation: bool = False,
    stream_logs: Optional[
        sdk_async.StreamConfig] = sdk_async.DEFAULT_STREAM_CONFIG
) -> Tuple[str, str]:
    """Async version of up() that spins up a service."""
    request_id = await asyncio.to_thread(sdk.up, task, service_name,
                                         _need_confirmation)
    if stream_logs is not None:
        return await sdk_async._stream_and_get(request_id, stream_logs)  # pylint: disable=protected-access
    else:
        return await sdk_async.get(request_id)


@usage_lib.entrypoint
async def update(
    task: Union['sky.Task', 'sky.Dag'],
    service_name: str,
    mode: 'serve_utils.UpdateMode',
    # Internal only:
    # pylint: disable=invalid-name
    _need_confirmation: bool = False,
    stream_logs: Optional[
        sdk_async.StreamConfig] = sdk_async.DEFAULT_STREAM_CONFIG
) -> None:
    """Async version of update() that updates an existing service."""
    request_id = await asyncio.to_thread(sdk.update, task, service_name, mode,
                                         _need_confirmation)
    if stream_logs is not None:
        return await sdk_async._stream_and_get(request_id, stream_logs)  # pylint: disable=protected-access
    else:
        return await sdk_async.get(request_id)


@usage_lib.entrypoint
async def down(
    service_names: Optional[Union[str, List[str]]],
    all: bool = False,  # pylint: disable=redefined-builtin
    purge: bool = False,
    stream_logs: Optional[
        sdk_async.StreamConfig] = sdk_async.DEFAULT_STREAM_CONFIG
) -> None:
    """Async version of down() that tears down a service."""
    request_id = await asyncio.to_thread(sdk.down, service_names, all, purge)
    if stream_logs is not None:
        return await sdk_async._stream_and_get(request_id, stream_logs)  # pylint: disable=protected-access
    else:
        return await sdk_async.get(request_id)


@usage_lib.entrypoint
async def terminate_replica(
    service_name: str,
    replica_id: int,
    purge: bool,
    stream_logs: Optional[
        sdk_async.StreamConfig] = sdk_async.DEFAULT_STREAM_CONFIG
) -> None:
    """Async version of terminate_replica() that tears down a specific
    replica."""
    request_id = await asyncio.to_thread(sdk.terminate_replica, service_name,
                                         replica_id, purge)
    if stream_logs is not None:
        return await sdk_async._stream_and_get(request_id, stream_logs)  # pylint: disable=protected-access
    else:
        return await sdk_async.get(request_id)


@usage_lib.entrypoint
async def status(
    service_names: Optional[Union[str, List[str]]],
    stream_logs: Optional[
        sdk_async.StreamConfig] = sdk_async.DEFAULT_STREAM_CONFIG
) -> List[Dict[str, Any]]:
    """Async version of status() that sdk_async.gets service statuses."""
    request_id = await asyncio.to_thread(sdk.status, service_names)
    if stream_logs is not None:
        return await sdk_async._stream_and_get(request_id, stream_logs)  # pylint: disable=protected-access
    else:
        return await sdk_async.get(request_id)


@usage_lib.entrypoint
async def tail_logs(service_name: str,
                    target: Union[str, 'serve_utils.ServiceComponent'],
                    replica_id: Optional[int] = None,
                    follow: bool = True,
                    output_stream: Optional['io.TextIOBase'] = None) -> None:
    """Async version of tail_logs() that tails logs for a service."""
    return await asyncio.to_thread(sdk.tail_logs, service_name, target,
                                   replica_id, follow, output_stream)


@usage_lib.entrypoint
async def sync_down_logs(service_name: str,
                         local_dir: str,
                         *,
                         targets: Optional[Union[
                             str, 'serve_utils.ServiceComponent', List[Union[
                                 str, 'serve_utils.ServiceComponent']]]] = None,
                         replica_ids: Optional[List[int]] = None) -> None:
    """Async version of sync_down_logs() that syncs down logs from service
      components."""
    return await asyncio.to_thread(sdk.sync_down_logs,
                                   service_name,
                                   local_dir,
                                   targets=targets,
                                   replica_ids=replica_ids)
