"""Utilities for streaming logs from response."""

import asyncio
import collections
import pathlib
from typing import AsyncGenerator, Deque, Optional

import aiofiles
import fastapi

from sky import sky_logging
from sky.server.requests import requests as requests_lib
from sky.utils import message_utils
from sky.utils import rich_utils

logger = sky_logging.init_logger(__name__)


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


async def log_streamer(request_id: Optional[str],
                       log_path: pathlib.Path,
                       plain_logs: bool = False,
                       tail: Optional[int] = None,
                       follow: bool = True) -> AsyncGenerator[str, None]:
    """Streams the logs of a request."""

    if request_id is not None:
        status_msg = rich_utils.EncodedStatusMessage(
            f'[dim]Checking request: {request_id}[/dim]')
        request_task = requests_lib.get_request(request_id)

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
        while request_task.status < requests_lib.RequestStatus.RUNNING:
            if request_task.status_msg is not None:
                waiting_msg = request_task.status_msg
            if show_request_waiting_spinner:
                yield status_msg.update(f'[dim]{waiting_msg}[/dim]')
            elif plain_logs and waiting_msg != last_waiting_msg:
                # Only log when waiting message changes.
                last_waiting_msg = waiting_msg
                # Use smaller padding (1024 bytes) to force browser rendering
                yield f'{waiting_msg}' + ' ' * 4096 + '\n'
            # Sleep shortly to avoid storming the DB and CPU and allow other
            # coroutines to run. This busy waiting loop is performance critical
            # for short-running requests, so we do not want to yield too long.
            await asyncio.sleep(0.1)
            request_task = requests_lib.get_request(request_id)
            if not follow:
                break
        if show_request_waiting_spinner:
            yield status_msg.stop()

    # Find last n lines of the log file. Do not read the whole file into memory.
    async with aiofiles.open(log_path, 'rb') as f:
        if tail is not None:
            # TODO(zhwu): this will include the control lines for rich status,
            # which may not lead to exact tail lines when showing on the client
            # side.
            lines: Deque[str] = collections.deque(maxlen=tail)
            async for line_str in _yield_log_file_with_payloads_skipped(f):
                lines.append(line_str)
            for line_str in lines:
                yield line_str

        while True:
            # Sleep 0 to yield control to allow other coroutines to run,
            # while keeps the loop tight to make log stream responsive.
            await asyncio.sleep(0)
            line: Optional[bytes] = await f.readline()
            if not line:
                if request_id is not None:
                    request_task = requests_lib.get_request(request_id)
                    if request_task.status > requests_lib.RequestStatus.RUNNING:
                        if (request_task.status ==
                                requests_lib.RequestStatus.CANCELLED):
                            yield (f'{request_task.name!r} request {request_id}'
                                   ' cancelled\n')
                        break
                if not follow:
                    break
                # Sleep shortly to avoid storming the DB and CPU, this has
                # little impact on the responsivness here since we are waiting
                # for a new line to come in.
                await asyncio.sleep(0.1)
                continue
            line_str = line.decode('utf-8')
            if plain_logs:
                is_payload, line_str = message_utils.decode_payload(
                    line_str, raise_for_mismatch=False)
                if is_payload:
                    continue
            yield line_str


def stream_response(
    request_id: str, logs_path: pathlib.Path,
    background_tasks: fastapi.BackgroundTasks
) -> fastapi.responses.StreamingResponse:

    async def on_disconnect():
        logger.info(f'User terminated the connection for request '
                    f'{request_id}')
        requests_lib.kill_requests([request_id])

    # The background task will be run after returning a response.
    # https://fastapi.tiangolo.com/tutorial/background-tasks/
    background_tasks.add_task(on_disconnect)

    return fastapi.responses.StreamingResponse(
        log_streamer(request_id, logs_path),
        media_type='text/plain',
        headers={
            'Cache-Control': 'no-cache, no-transform',
            'X-Accel-Buffering': 'no',
            'Transfer-Encoding': 'chunked'
        })
