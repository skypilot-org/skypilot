"""Async client-side Python SDK for SkyPilot.

All functions will return a future that can be awaited on.

Usage example:

.. code-block:: python

    request_id = await sky.status()
    statuses = await sky.get(request_id)

"""
import logging
import typing
from typing import Any, Dict, List, Optional, Tuple, Union

import aiohttp
import colorama

from sky import admin_policy
from sky import backends
from sky import exceptions
from sky import models
from sky import sky_logging
import sky.catalog
from sky.client import common as client_common
from sky.client import sdk
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.server import common as server_common
from sky.server import rest
from sky.server.requests import payloads
from sky.server.requests import requests as requests_lib
from sky.skylet import job_lib
from sky.usage import usage_lib
from sky.utils import annotations
from sky.utils import common
from sky.utils import context_utils
from sky.utils import env_options
from sky.utils import message_utils
from sky.utils import rich_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import io

    import sky

logger = sky_logging.init_logger(__name__)
logging.getLogger('httpx').setLevel(logging.CRITICAL)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def get(request_id: str) -> Any:
    """Async version of get() that waits for and gets the result of a request.

    Args:
        request_id: The request ID of the request to get.

    Returns:
        The ``Request Returns`` of the specified request. See the documentation
        of the specific requests above for more details.

    Raises:
        Exception: It raises the same exceptions as the specific requests,
            see ``Request Raises`` in the documentation of the specific requests
            above.
    """
    async with aiohttp.ClientSession() as session:
        response = await server_common.make_authenticated_request_async(
            session,
            'GET',
            f'/api/get?request_id={request_id}',
            retry=False,
            timeout=aiohttp.ClientTimeout(
                total=None,
                connect=client_common.
                API_SERVER_REQUEST_CONNECTION_TIMEOUT_SECONDS))

        try:
            request_task = None
            if response.status == 200:
                request_task = requests_lib.Request.decode(
                    payloads.RequestPayload(**await response.json()))
            elif response.status == 500:
                try:
                    request_task = requests_lib.Request.decode(
                        payloads.RequestPayload(**await response.json()))
                    logger.debug(f'Got request with error: {request_task.name}')
                except Exception:  # pylint: disable=broad-except
                    request_task = None
            if request_task is None:
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(
                        f'Failed to get request {request_id}: '
                        f'{response.status} {await response.text()}')
            error = request_task.get_error()
            if error is not None:
                error_obj = error['object']
                if env_options.Options.SHOW_DEBUG_INFO.get():
                    stacktrace = getattr(error_obj, 'stacktrace',
                                         str(error_obj))
                    logger.error('=== Traceback on SkyPilot API Server ===\n'
                                 f'{stacktrace}')
                with ux_utils.print_exception_no_traceback():
                    raise error_obj
            if request_task.status == requests_lib.RequestStatus.CANCELLED:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.RequestCancelled(
                        f'{colorama.Fore.YELLOW}Current {request_task.name!r} '
                        f'request ({request_task.request_id}) is cancelled by '
                        f'another process. {colorama.Style.RESET_ALL}')
            return request_task.get_return_value()
        finally:
            response.close()


async def decode_rich_status_async(
        response: 'aiohttp.ClientResponse'
) -> typing.AsyncIterator[Optional[str]]:
    """Async version of rich_utils.decode_rich_status that decodes rich status
    messages from an aiohttp response.

    Args:
        response: The aiohttp response.

    Yields:
        Optional[str]: Decoded lines or None for control messages.
    """
    decoding_status = None
    try:
        last_line = ''
        # Buffer to store incomplete UTF-8 bytes between chunks
        undecoded_buffer = b''

        # Iterate over the response content in chunks
        async for chunk in response.content.iter_chunked(8192):
            if chunk is None:
                return

            # Append the new chunk to any leftover bytes from previous iteration
            current_bytes = undecoded_buffer + chunk
            undecoded_buffer = b''

            # Try to decode the combined bytes
            try:
                encoded_msg = current_bytes.decode('utf-8')
            except UnicodeDecodeError as e:
                # Check if this is potentially an incomplete sequence at the end
                if e.start > 0:
                    # Decode the valid part
                    encoded_msg = current_bytes[:e.start].decode('utf-8')

                    # Check if the remaining bytes are likely a partial char
                    # or actually invalid UTF-8
                    remaining_bytes = current_bytes[e.start:]
                    if len(remaining_bytes) < 4:  # Max UTF-8 char is 4 bytes
                        # Likely incomplete - save for next chunk
                        undecoded_buffer = remaining_bytes
                    else:
                        # Likely invalid - replace with replacement character
                        encoded_msg += remaining_bytes.decode('utf-8',
                                                              errors='replace')
                        undecoded_buffer = b''
                else:
                    # Error at the very beginning of the buffer - invalid UTF-8
                    encoded_msg = current_bytes.decode('utf-8',
                                                       errors='replace')
                    undecoded_buffer = b''

            lines = encoded_msg.splitlines(keepends=True)

            # Skip processing if lines is empty to avoid IndexError
            if not lines:
                continue

            lines[0] = last_line + lines[0]
            last_line = lines[-1]
            # If the last line is not ended with `\r` or `\n` (with ending
            # spaces stripped), it means the last line is not a complete line.
            # We keep the last line in the buffer and continue.
            if (not last_line.strip(' ').endswith('\r') and
                    not last_line.strip(' ').endswith('\n')):
                lines = lines[:-1]
            else:
                # Reset the buffer for the next line, as the last line is a
                # complete line.
                last_line = ''

            for line in lines:
                if line.endswith('\r\n'):
                    # Replace `\r\n` with `\n`, as printing a line ends with
                    # `\r\n` in linux will cause the line to be empty.
                    line = line[:-2] + '\n'
                is_payload, line = message_utils.decode_payload(
                    line, raise_for_mismatch=False)
                control = None
                if is_payload:
                    control, encoded_status = rich_utils.Control.decode(line)
                if control is None:
                    yield line
                    continue

                if control == rich_utils.Control.RETRY:
                    raise exceptions.RequestInterruptedError(
                        'Streaming interrupted. Please retry.')
                # control is not None, i.e. it is a rich status control message.
                # In async context, we'll handle rich status controls normally
                # since async typically runs in main thread
                if control == rich_utils.Control.INIT:
                    decoding_status = rich_utils.client_status(encoded_status)
                else:
                    if decoding_status is None:
                        # status may not be initialized if a user use --tail for
                        # sky api logs.
                        continue
                    assert decoding_status is not None, (
                        f'Rich status not initialized: {line}')
                    if control == rich_utils.Control.UPDATE:
                        decoding_status.update(encoded_status)
                    elif control == rich_utils.Control.STOP:
                        decoding_status.stop()
                    elif control == rich_utils.Control.EXIT:
                        decoding_status.__exit__(None, None, None)
                    elif control == rich_utils.Control.START:
                        decoding_status.start()
                    elif control == rich_utils.Control.HEARTBEAT:
                        # Heartbeat is not displayed to the user, so we do not
                        # need to update the status.
                        pass
    finally:
        if decoding_status is not None:
            decoding_status.__exit__(None, None, None)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def stream_response_async(request_id: Optional[str],
                                response: 'aiohttp.ClientResponse',
                                output_stream: Optional['io.TextIOBase'] = None,
                                resumable: bool = False) -> Any:
    """Async version of stream_response that streams the response to the
    console.

    Args:
        request_id: The request ID.
        response: The aiohttp response.
        output_stream: The output stream to write to. If None, print to the
            console.
        resumable: Whether the response is resumable on retry. If True, the
            streaming will start from the previous failure point on retry.
    """

    retry_context: Optional[rest.RetryContext] = None
    if resumable:
        retry_context = rest.get_retry_context()
    try:
        line_count = 0
        async for line in decode_rich_status_async(response):
            if line is not None:
                line_count += 1
                if retry_context is None:
                    print(line, flush=True, end='', file=output_stream)
                elif line_count > retry_context.line_processed:
                    print(line, flush=True, end='', file=output_stream)
                    retry_context.line_processed = line_count
        if request_id is not None:
            return await get(request_id)
    except Exception:  # pylint: disable=broad-except
        logger.debug(f'To stream request logs: sky api logs {request_id}')
        raise


async def stream_and_get(
    request_id: Optional[str] = None,
    log_path: Optional[str] = None,
    tail: Optional[int] = None,
    follow: bool = True,
    output_stream: Optional['io.TextIOBase'] = None,
) -> Any:
    """Streams the logs of a request or a log file and gets the final result.

    This will block until the request is finished. The request id can be a
    prefix of the full request id.

    Args:
        request_id: The prefix of the request ID of the request to stream.
        log_path: The path to the log file to stream.
        tail: The number of lines to show from the end of the logs.
            If None, show all logs.
        follow: Whether to follow the logs.
        output_stream: The output stream to write to. If None, print to the
            console.

    Returns:
        The ``Request Returns`` of the specified request. See the documentation
        of the specific requests above for more details.

    Raises:
        Exception: It raises the same exceptions as the specific requests,
            see ``Request Raises`` in the documentation of the specific requests
            above.
    """
    params = {
        'request_id': request_id,
        'log_path': log_path,
        'tail': str(tail) if tail is not None else None,
        'follow': str(follow).lower(),  # Convert boolean to string for aiohttp
        'format': 'console',
    }
    # Filter out None values
    params = {k: v for k, v in params.items() if v is not None}

    async with aiohttp.ClientSession() as session:
        response = await server_common.make_authenticated_request_async(
            session,
            'GET',
            '/api/stream',
            params=params,
            retry=False,
            timeout=aiohttp.ClientTimeout(
                total=None,
                connect=client_common.
                API_SERVER_REQUEST_CONNECTION_TIMEOUT_SECONDS))

        try:
            if response.status in [404, 400]:
                detail = (await response.json()).get('detail')
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(f'Failed to stream logs: {detail}')
            elif response.status != 200:
                return await get(request_id)

            return await stream_response_async(request_id, response,
                                               output_stream)
        finally:
            response.close()


@usage_lib.entrypoint
@annotations.client_api
async def check(infra_list: Optional[Tuple[str, ...]],
                verbose: bool,
                workspace: Optional[str] = None,
                stream_logs: bool = True) -> Dict[str, List[str]]:
    """Async version of check() that checks the credentials to enable clouds."""
    request_id = await context_utils.to_thread(sdk.check, infra_list, verbose,
                                               workspace)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def enabled_clouds(workspace: Optional[str] = None,
                         expand: bool = False,
                         stream_logs: bool = True) -> List[str]:
    """Async version of enabled_clouds() that gets the enabled clouds."""
    request_id = await context_utils.to_thread(sdk.enabled_clouds, workspace,
                                               expand)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def list_accelerators(
    gpus_only: bool = True,
    name_filter: Optional[str] = None,
    region_filter: Optional[str] = None,
    quantity_filter: Optional[int] = None,
    clouds: Optional[Union[List[str], str]] = None,
    all_regions: bool = False,
    require_price: bool = True,
    case_sensitive: bool = True,
    stream_logs: bool = True
) -> Dict[str, List[sky.catalog.common.InstanceTypeInfo]]:
    """Async version of list_accelerators() that lists the names of all
    accelerators offered by Sky."""
    request_id = await context_utils.to_thread(sdk.list_accelerators, gpus_only,
                                               name_filter, region_filter,
                                               quantity_filter, clouds,
                                               all_regions, require_price,
                                               case_sensitive)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def list_accelerator_counts(
        gpus_only: bool = True,
        name_filter: Optional[str] = None,
        region_filter: Optional[str] = None,
        quantity_filter: Optional[int] = None,
        clouds: Optional[Union[List[str], str]] = None,
        stream_logs: bool = True) -> Dict[str, List[int]]:
    """Async version of list_accelerator_counts() that lists all accelerators
      offered by Sky and available counts."""
    request_id = await context_utils.to_thread(sdk.list_accelerator_counts,
                                               gpus_only, name_filter,
                                               region_filter, quantity_filter,
                                               clouds)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def optimize(dag: 'sky.Dag',
                   minimize: common.OptimizeTarget = common.OptimizeTarget.COST,
                   admin_policy_request_options: Optional[
                       admin_policy.RequestOptions] = None,
                   stream_logs: bool = True) -> sky.dag.Dag:
    """Async version of optimize() that finds the best execution plan for the
      given DAG."""
    request_id = await context_utils.to_thread(sdk.optimize, dag, minimize,
                                               admin_policy_request_options)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def workspaces(stream_logs: bool = True) -> Dict[str, Any]:
    """Async version of workspaces() that gets the workspaces."""
    request_id = await context_utils.to_thread(sdk.workspaces)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def launch(
    task: Union['sky.Task', 'sky.Dag'],
    cluster_name: Optional[str] = None,
    retry_until_up: bool = False,
    idle_minutes_to_autostop: Optional[int] = None,
    dryrun: bool = False,
    down: bool = False,  # pylint: disable=redefined-outer-name
    backend: Optional[backends.Backend] = None,
    optimize_target: common.OptimizeTarget = common.OptimizeTarget.COST,
    no_setup: bool = False,
    clone_disk_from: Optional[str] = None,
    fast: bool = False,
    # Internal only:
    # pylint: disable=invalid-name
    _need_confirmation: bool = False,
    _is_launched_by_jobs_controller: bool = False,
    _is_launched_by_sky_serve_controller: bool = False,
    _disable_controller_check: bool = False,
    stream_logs: bool = True,
) -> Tuple[Optional[int], Optional[backends.ResourceHandle]]:
    """Async version of launch() that launches a cluster or task."""
    request_id = await context_utils.to_thread(
        sdk.launch, task, cluster_name, retry_until_up,
        idle_minutes_to_autostop, dryrun, down, backend, optimize_target,
        no_setup, clone_disk_from, fast, _need_confirmation,
        _is_launched_by_jobs_controller, _is_launched_by_sky_serve_controller,
        _disable_controller_check)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def exec(  # pylint: disable=redefined-builtin
    task: Union['sky.Task', 'sky.Dag'],
    cluster_name: Optional[str] = None,
    dryrun: bool = False,
    down: bool = False,  # pylint: disable=redefined-outer-name
    backend: Optional[backends.Backend] = None,
    stream_logs: bool = True,
) -> Tuple[Optional[int], Optional[backends.ResourceHandle]]:
    """Async version of exec() that executes a task on an existing cluster."""
    request_id = await context_utils.to_thread(sdk.exec, task, cluster_name,
                                               dryrun, down, backend)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def tail_logs(cluster_name: str,
                    job_id: Optional[int],
                    follow: bool,
                    tail: int = 0,
                    output_stream: Optional['io.TextIOBase'] = None) -> int:
    """Async version of tail_logs() that tails the logs of a job."""
    return await context_utils.to_thread(sdk.tail_logs, cluster_name, job_id,
                                         follow, tail, output_stream)


@usage_lib.entrypoint
@annotations.client_api
async def download_logs(cluster_name: str,
                        job_ids: Optional[List[str]]) -> Dict[str, str]:
    """Async version of download_logs() that downloads the logs of jobs."""
    return await context_utils.to_thread(sdk.download_logs, cluster_name,
                                         job_ids)


@usage_lib.entrypoint
@annotations.client_api
async def start(
    cluster_name: str,
    idle_minutes_to_autostop: Optional[int] = None,
    retry_until_up: bool = False,
    down: bool = False,  # pylint: disable=redefined-outer-name
    force: bool = False,
    stream_logs: bool = True,
) -> backends.CloudVmRayResourceHandle:
    """Async version of start() that restarts a cluster."""
    request_id = await context_utils.to_thread(sdk.start, cluster_name,
                                               idle_minutes_to_autostop,
                                               retry_until_up, down, force)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def down(cluster_name: str,
               purge: bool = False,
               stream_logs: bool = True) -> None:
    """Async version of down() that tears down a cluster."""
    request_id = await context_utils.to_thread(sdk.down, cluster_name, purge)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def stop(cluster_name: str,
               purge: bool = False,
               stream_logs: bool = True) -> None:
    """Async version of stop() that stops a cluster."""
    request_id = await context_utils.to_thread(sdk.stop, cluster_name, purge)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def autostop(
        cluster_name: str,
        idle_minutes: int,
        down: bool = False,  # pylint: disable=redefined-outer-name
        stream_logs: bool = True) -> None:
    """Async version of autostop() that schedules an autostop/autodown for a
      cluster."""
    request_id = await context_utils.to_thread(sdk.autostop, cluster_name,
                                               idle_minutes, down)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def queue(cluster_name: str,
                skip_finished: bool = False,
                all_users: bool = False,
                stream_logs: bool = True) -> List[dict]:
    """Async version of queue() that gets the job queue of a cluster."""
    request_id = await context_utils.to_thread(sdk.queue, cluster_name,
                                               skip_finished, all_users)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def job_status(
    cluster_name: str,
    job_ids: Optional[List[int]] = None,
    stream_logs: bool = True
) -> Dict[Optional[int], Optional[job_lib.JobStatus]]:
    """Async version of job_status() that gets the status of jobs on a
      cluster."""
    request_id = await context_utils.to_thread(sdk.job_status, cluster_name,
                                               job_ids)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def cancel(
        cluster_name: str,
        all: bool = False,  # pylint: disable=redefined-builtin
        all_users: bool = False,
        job_ids: Optional[List[int]] = None,
        # pylint: disable=invalid-name
        _try_cancel_if_cluster_is_init: bool = False,
        stream_logs: bool = True) -> None:
    """Async version of cancel() that cancels jobs on a cluster."""
    request_id = await context_utils.to_thread(sdk.cancel, cluster_name, all,
                                               all_users, job_ids,
                                               _try_cancel_if_cluster_is_init)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def status(
    cluster_names: Optional[List[str]] = None,
    refresh: common.StatusRefreshMode = common.StatusRefreshMode.NONE,
    all_users: bool = False,
    stream_logs: bool = True,
) -> List[Dict[str, Any]]:
    """Async version of status() that gets cluster statuses."""
    request_id = await context_utils.to_thread(sdk.status, cluster_names,
                                               refresh, all_users)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def endpoints(cluster: str,
                    port: Optional[Union[int, str]] = None,
                    stream_logs: bool = True) -> Dict[int, str]:
    """Async version of endpoints() that gets the endpoint for a given cluster
      and port number."""
    request_id = await context_utils.to_thread(sdk.endpoints, cluster, port)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def cost_report(stream_logs: bool = True) -> List[Dict[str, Any]]:
    """Async version of cost_report() that gets all cluster cost reports."""
    request_id = await context_utils.to_thread(sdk.cost_report)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def storage_ls(stream_logs: bool = True) -> List[Dict[str, Any]]:
    """Async version of storage_ls() that gets the storages."""
    request_id = await context_utils.to_thread(sdk.storage_ls)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def storage_delete(name: str, stream_logs: bool = True) -> None:
    """Async version of storage_delete() that deletes a storage."""
    request_id = await context_utils.to_thread(sdk.storage_delete, name)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def local_up(gpus: bool,
                   ips: Optional[List[str]],
                   ssh_user: Optional[str],
                   ssh_key: Optional[str],
                   cleanup: bool,
                   context_name: Optional[str] = None,
                   password: Optional[str] = None,
                   stream_logs: bool = True) -> None:
    """Async version of local_up() that launches a Kubernetes cluster on
    local machines."""
    request_id = await context_utils.to_thread(sdk.local_up, gpus, ips,
                                               ssh_user, ssh_key, cleanup,
                                               context_name, password)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def local_down(stream_logs: bool = True) -> None:
    """Async version of local_down() that tears down the Kubernetes cluster
    started by local_up."""
    request_id = await context_utils.to_thread(sdk.local_down)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def ssh_up(infra: Optional[str] = None, stream_logs: bool = True) -> None:
    """Async version of ssh_up() that deploys the SSH Node Pools defined in
      ~/.sky/ssh_targets.yaml."""
    request_id = await context_utils.to_thread(sdk.ssh_up, infra)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def ssh_down(infra: Optional[str] = None,
                   stream_logs: bool = True) -> None:
    """Async version of ssh_down() that tears down a Kubernetes cluster on SSH
    targets."""
    request_id = await context_utils.to_thread(sdk.ssh_down, infra)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def realtime_kubernetes_gpu_availability(
    context: Optional[str] = None,
    name_filter: Optional[str] = None,
    quantity_filter: Optional[int] = None,
    is_ssh: Optional[bool] = None,
    stream_logs: bool = True
) -> List[Tuple[str, List[models.RealtimeGpuAvailability]]]:
    """Async version of realtime_kubernetes_gpu_availability() that gets the
      real-time Kubernetes GPU availability."""
    request_id = await context_utils.to_thread(
        sdk.realtime_kubernetes_gpu_availability, context, name_filter,
        quantity_filter, is_ssh)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def kubernetes_node_info(
        context: Optional[str] = None,
        stream_logs: bool = True) -> models.KubernetesNodesInfo:
    """Async version of kubernetes_node_info() that gets the resource
    information for all the nodes in the cluster."""
    request_id = await context_utils.to_thread(sdk.kubernetes_node_info,
                                               context)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def status_kubernetes(
    stream_logs: bool = True
) -> Tuple[List[kubernetes_utils.KubernetesSkyPilotClusterInfoPayload],
           List[kubernetes_utils.KubernetesSkyPilotClusterInfoPayload],
           List[Dict[str, Any]], Optional[str]]:
    """Async version of status_kubernetes() that gets all SkyPilot clusters
      and jobs in the Kubernetes cluster."""
    request_id = await context_utils.to_thread(sdk.status_kubernetes)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def api_cancel(request_ids: Optional[Union[str, List[str]]] = None,
                     all_users: bool = False,
                     silent: bool = False,
                     stream_logs: bool = True) -> List[str]:
    """Async version of api_cancel() that aborts a request or all requests."""
    request_id = await context_utils.to_thread(sdk.api_cancel, request_ids,
                                               all_users, silent)
    if stream_logs:
        return await stream_and_get(request_id)
    else:
        return await get(request_id)


@usage_lib.entrypoint
@annotations.client_api
async def api_status(request_ids: Optional[List[str]] = None,
                     all_status: bool = False) -> List[payloads.RequestPayload]:
    """Async version of api_status() that lists all requests."""
    return await context_utils.to_thread(sdk.api_status, request_ids,
                                         all_status)


@usage_lib.entrypoint
@annotations.client_api
async def dashboard(starting_page: Optional[str] = None) -> None:
    """Async version of dashboard() that starts the dashboard for SkyPilot."""
    return await context_utils.to_thread(sdk.dashboard, starting_page)


@usage_lib.entrypoint
@annotations.client_api
async def api_info() -> Dict[str, Any]:
    """Async version of api_info() that gets the server's status, commit and
      version."""
    return await context_utils.to_thread(sdk.api_info)


@usage_lib.entrypoint
@annotations.client_api
async def api_stop() -> None:
    """Async version of api_stop() that stops the API server."""
    return await context_utils.to_thread(sdk.api_stop)


@usage_lib.entrypoint
@annotations.client_api
async def api_server_logs(follow: bool = True,
                          tail: Optional[int] = None) -> None:
    """Async version of api_server_logs() that streams the API server logs."""
    return await context_utils.to_thread(sdk.api_server_logs, follow, tail)


@usage_lib.entrypoint
@annotations.client_api
async def api_login(endpoint: Optional[str] = None,
                    get_token: bool = False) -> None:
    """Async version of api_login() that logs into a SkyPilot API server."""
    return await context_utils.to_thread(sdk.api_login, endpoint, get_token)
