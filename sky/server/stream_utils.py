"""Utilities for streaming logs from response."""

import asyncio
import collections
import pathlib
from typing import AsyncGenerator, Deque, List, Optional

import aiofiles
import fastapi

from sky import global_user_state
from sky import sky_logging
from sky.server.requests import requests as requests_lib
from sky.utils import common_utils
from sky.utils import message_utils
from sky.utils import rich_utils
from sky.utils import status_lib

logger = sky_logging.init_logger(__name__)

# When streaming log lines, buffer the lines in memory and flush them in chunks
# to improve log tailing throughput. Buffer size is the max size bytes of each
# chunk and the timeout threshold for flushing the buffer to ensure
# responsiveness.
_BUFFER_SIZE = 8 * 1024  # 8KB
_BUFFER_TIMEOUT = 0.02  # 20ms
_HEARTBEAT_INTERVAL = 30

LONG_REQUEST_POLL_INTERVAL = 1
DEFAULT_POLL_INTERVAL = 0.1


async def _yield_log_file_with_payloads_skipped(
        log_file) -> AsyncGenerator[str, None]:
    async for line in log_file:
        if not line:
            return
        is_payload, line_str = message_utils.decode_payload(
            line.decode('utf-8'), raise_for_mismatch=False)
        if is_payload:
            continue

        yield line_str


async def log_streamer(
    request_id: Optional[str],
    log_path: pathlib.Path,
    plain_logs: bool = False,
    tail: Optional[int] = None,
    follow: bool = True,
    cluster_name: Optional[str] = None,
    polling_interval: float = DEFAULT_POLL_INTERVAL
) -> AsyncGenerator[str, None]:
    """Streams the logs of a request.

    Args:
        request_id: The request ID to check whether the log tailing process
            should be stopped.
        log_path: The path to the log file.
        plain_logs: Whether to show plain logs.
        tail: The number of lines to tail. If None, tail the whole file.
        follow: Whether to follow the log file.
        cluster_name: The cluster name to check status for provision logs.
            If provided and cluster status is UP, streaming will terminate.
    """

    if request_id is not None:
        status_msg = rich_utils.EncodedStatusMessage(
            f'[dim]Checking request: {request_id}[/dim]')
        request_task = await requests_lib.get_request_async(request_id)

        if request_task is None:
            raise fastapi.HTTPException(
                status_code=404, detail=f'Request {request_id} not found')
        request_id = request_task.request_id

        # Do not show the waiting spinner if the request is a fast, non-blocking
        # request.
        show_request_waiting_spinner = (not plain_logs and
                                        request_task.schedule_type
                                        == requests_lib.ScheduleType.LONG)

        if show_request_waiting_spinner:
            yield status_msg.init()
            yield status_msg.start()
        last_waiting_msg = ''
        waiting_msg = (f'Waiting for {request_task.name!r} request to be '
                       f'scheduled: {request_id}')
        req_status = request_task.status
        req_msg = request_task.status_msg
        # Slowly back off the database polling up to every 1 second, to avoid
        # overloading the CPU and DB.
        backoff = common_utils.Backoff(initial_backoff=polling_interval,
                                       max_backoff_factor=10,
                                       multiplier=1.2)
        while req_status < requests_lib.RequestStatus.RUNNING:
            if req_msg is not None:
                waiting_msg = request_task.status_msg
            if show_request_waiting_spinner:
                yield status_msg.update(f'[dim]{waiting_msg}[/dim]')
            elif plain_logs and waiting_msg != last_waiting_msg:
                # Only log when waiting message changes.
                last_waiting_msg = waiting_msg
                # Use smaller padding (1024 bytes) to force browser rendering
                yield f'{waiting_msg}' + ' ' * 4096 + '\n'
            # Sleep shortly to avoid storming the DB and CPU and allow other
            # coroutines to run.
            # TODO(aylei): we should use a better mechanism to avoid busy
            # polling the DB, which can be a bottleneck for high-concurrency
            # requests.
            await asyncio.sleep(backoff.current_backoff())
            status_with_msg = await requests_lib.get_request_status_async(
                request_id, include_msg=True)
            req_status = status_with_msg.status
            req_msg = status_with_msg.status_msg
            if not follow:
                break
        if show_request_waiting_spinner:
            yield status_msg.stop()

    async with aiofiles.open(log_path, 'rb') as f:
        async for chunk in _tail_log_file(f, request_id, plain_logs, tail,
                                          follow, cluster_name,
                                          polling_interval):
            yield chunk


async def _tail_log_file(
    f: aiofiles.threadpool.binary.AsyncBufferedReader,
    request_id: Optional[str] = None,
    plain_logs: bool = False,
    tail: Optional[int] = None,
    follow: bool = True,
    cluster_name: Optional[str] = None,
    polling_interval: float = DEFAULT_POLL_INTERVAL
) -> AsyncGenerator[str, None]:
    """Tail the opened log file, buffer the lines and flush in chunks."""

    if tail is not None:
        # Find last n lines of the log file. Do not read the whole file into
        # memory.
        # TODO(zhwu): this will include the control lines for rich status,
        # which may not lead to exact tail lines when showing on the client
        # side.
        lines: Deque[str] = collections.deque(maxlen=tail)
        async for line_str in _yield_log_file_with_payloads_skipped(f):
            lines.append(line_str)
        for line_str in lines:
            yield line_str

    last_heartbeat_time = asyncio.get_event_loop().time()
    last_status_check_time = asyncio.get_event_loop().time()

    # Buffer the lines in memory and flush them in chunks to improve log
    # tailing throughput.
    buffer: List[str] = []
    buffer_bytes = 0
    last_flush_time = asyncio.get_event_loop().time()

    async def flush_buffer() -> AsyncGenerator[str, None]:
        nonlocal buffer, buffer_bytes, last_flush_time
        if buffer:
            yield ''.join(buffer)
            buffer.clear()
            buffer_bytes = 0
            last_flush_time = asyncio.get_event_loop().time()

    while True:
        # Sleep 0 to yield control to allow other coroutines to run,
        # while keeps the loop tight to make log stream responsive.
        await asyncio.sleep(0)
        current_time = asyncio.get_event_loop().time()
        # Flush the buffer when it is not empty and the buffer is full or the
        # flush timeout is reached.
        if buffer and (buffer_bytes >= _BUFFER_SIZE or
                       (current_time - last_flush_time) >= _BUFFER_TIMEOUT):
            async for chunk in flush_buffer():
                yield chunk

        line: Optional[bytes] = await f.readline()
        if not line:
            # Avoid checking the status too frequently to avoid overloading the
            # DB.
            should_check_status = (current_time -
                                   last_status_check_time) >= polling_interval
            if not follow:
                # We will only hit this path once, but we should make sure to
                # check the status so that we display the final request status
                # if the request is complete.
                should_check_status = True
            if request_id is not None and should_check_status:
                last_status_check_time = current_time
                req_status = await requests_lib.get_request_status_async(
                    request_id)
                if req_status.status > requests_lib.RequestStatus.RUNNING:
                    if (req_status.status ==
                            requests_lib.RequestStatus.CANCELLED):
                        request_task = await requests_lib.get_request_async(
                            request_id)
                        if request_task.should_retry:
                            buffer.append(
                                message_utils.encode_payload(
                                    rich_utils.Control.RETRY.encode('')))
                        else:
                            buffer.append(
                                f'{request_task.name!r} request {request_id}'
                                ' cancelled\n')
                    break
            if not follow:
                # The below checks (cluster status, heartbeat) are not needed
                # for non-follow logs.
                break
            # Provision logs pass in cluster_name, check cluster status
            # periodically to see if provisioning is done.
            if cluster_name is not None and should_check_status:
                last_status_check_time = current_time
                cluster_record = await (
                    global_user_state.get_status_from_cluster_name_async(
                        cluster_name))
                if (cluster_record is None or
                        cluster_record != status_lib.ClusterStatus.INIT):
                    break
            if current_time - last_heartbeat_time >= _HEARTBEAT_INTERVAL:
                # Currently just used to keep the connection busy, refer to
                # https://github.com/skypilot-org/skypilot/issues/5750 for
                # more details.
                buffer.append(
                    message_utils.encode_payload(
                        rich_utils.Control.HEARTBEAT.encode('')))
                last_heartbeat_time = current_time

            # Sleep shortly to avoid storming the DB and CPU, this has
            # little impact on the responsivness here since we are waiting
            # for a new line to come in.
            await asyncio.sleep(0.1)
            continue

        # Refresh the heartbeat time, this is a trivial optimization for
        # performance but it helps avoid unnecessary heartbeat strings
        # being printed when the client runs in an old version.
        last_heartbeat_time = asyncio.get_event_loop().time()
        line_str = line.decode('utf-8')
        if plain_logs:
            is_payload, line_str = message_utils.decode_payload(
                line_str, raise_for_mismatch=False)
            # TODO(aylei): implement heartbeat mechanism for plain logs,
            # sending invisible characters might be okay.
            if is_payload:
                continue
        buffer.append(line_str)
        buffer_bytes += len(line_str.encode('utf-8'))

    # Flush remaining lines in the buffer.
    async for chunk in flush_buffer():
        yield chunk


def stream_response_for_long_request(
    request_id: str,
    logs_path: pathlib.Path,
    background_tasks: fastapi.BackgroundTasks,
) -> fastapi.responses.StreamingResponse:
    return stream_response(request_id,
                           logs_path,
                           background_tasks,
                           polling_interval=LONG_REQUEST_POLL_INTERVAL)


def stream_response(
    request_id: str,
    logs_path: pathlib.Path,
    background_tasks: fastapi.BackgroundTasks,
    polling_interval: float = DEFAULT_POLL_INTERVAL
) -> fastapi.responses.StreamingResponse:

    async def on_disconnect():
        logger.info(f'User terminated the connection for request '
                    f'{request_id}')
        requests_lib.kill_requests([request_id])

    # The background task will be run after returning a response.
    # https://fastapi.tiangolo.com/tutorial/background-tasks/
    background_tasks.add_task(on_disconnect)

    return fastapi.responses.StreamingResponse(
        log_streamer(request_id, logs_path, polling_interval=polling_interval),
        media_type='text/plain',
        headers={
            'Cache-Control': 'no-cache, no-transform',
            'X-Accel-Buffering': 'no',
            'Transfer-Encoding': 'chunked'
        })
