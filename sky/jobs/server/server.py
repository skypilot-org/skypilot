"""REST API for managed jobs."""
import os

import fastapi
import httpx

from sky import sky_logging
from sky.jobs.server import core
from sky.jobs.server import dashboard_utils
from sky.server import common as server_common
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import requests
from sky.skylet import constants
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)

router = fastapi.APIRouter()


@router.post('/launch')
async def launch(request: fastapi.Request,
                 jobs_launch_body: payloads.JobsLaunchBody) -> None:

    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='jobs.launch',
        request_body=jobs_launch_body,
        func=core.launch,
        schedule_type=requests.ScheduleType.BLOCKING,
    )


@router.post('/queue')
async def queue(request: fastapi.Request,
                jobs_queue_body: payloads.JobsQueueBody) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='jobs.queue',
        request_body=jobs_queue_body,
        func=core.queue,
        schedule_type=(requests.ScheduleType.BLOCKING if jobs_queue_body.refresh
                       else requests.ScheduleType.NON_BLOCKING),
    )


@router.post('/cancel')
async def cancel(request: fastapi.Request,
                 jobs_cancel_body: payloads.JobsCancelBody) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='jobs.cancel',
        request_body=jobs_cancel_body,
        func=core.cancel,
        schedule_type=requests.ScheduleType.NON_BLOCKING,
    )


@router.post('/logs')
async def logs(
    request: fastapi.Request,
    jobs_logs_body: payloads.JobsLogsBody,
) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='jobs.logs',
        request_body=jobs_logs_body,
        func=core.tail_logs,
        schedule_type=requests.ScheduleType.NON_BLOCKING,
    )


@router.get('/dashboard')
async def dashboard(request: fastapi.Request,
                    user_hash: str) -> fastapi.Response:
    # Find the port for the dashboard of the user
    os.environ[constants.USER_ID_ENV_VAR] = user_hash
    server_common.reload_for_new_request()
    logger.info(f'Starting dashboard for user hash: {user_hash}')

    body = payloads.RequestBody()
    body.env_vars[constants.USER_ID_ENV_VAR] = user_hash
    body.entrypoint_command = 'jobs.dashboard'
    body.override_skypilot_config = {}

    with dashboard_utils.get_dashboard_lock_for_user(user_hash):
        max_retries = 3
        for attempt in range(max_retries):
            port, pid = dashboard_utils.get_dashboard_session(user_hash)
            if port == 0 or attempt > 0:
                # Let the client know that we are waiting for starting the
                # dashboard.
                try:
                    port, pid = core.start_dashboard_forwarding()
                except Exception as e:  # pylint: disable=broad-except
                    # We catch all exceptions to gracefully handle unknown
                    # errors and raise an HTTPException to the client.
                    msg = (
                        'Dashboard failed to start: '
                        f'{common_utils.format_exception(e, use_bracket=True)}')
                    logger.error(msg)
                    raise fastapi.HTTPException(status_code=503, detail=msg)
                dashboard_utils.add_dashboard_session(user_hash, port, pid)

            # Assuming the dashboard is forwarded to localhost on the API server
            dashboard_url = f'http://localhost:{port}'
            try:
                # Ping the dashboard to check if it's still running
                async with httpx.AsyncClient() as client:
                    response = await client.request('GET',
                                                    dashboard_url,
                                                    timeout=1)
                break  # Connection successful, proceed with the request
            except Exception as e:  # pylint: disable=broad-except
                # We catch all exceptions to gracefully handle unknown
                # errors and retry or raise an HTTPException to the client.
                msg = (
                    f'Dashboard connection attempt {attempt + 1} failed with '
                    f'{common_utils.format_exception(e, use_bracket=True)}')
                logger.info(msg)
                if attempt == max_retries - 1:
                    raise fastapi.HTTPException(status_code=503, detail=msg)

    # Create a client session to forward the request
    try:
        async with httpx.AsyncClient() as client:
            # Make the request and get the response
            response = await client.request(
                method='GET',
                url=f'{dashboard_url}',
                headers=request.headers.raw,
            )

            # Create a new response with the content already read
            content = await response.aread()
            return fastapi.Response(
                content=content,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.headers.get('content-type'))
    except Exception as e:
        msg = (f'Failed to forward request to dashboard: '
               f'{common_utils.format_exception(e, use_bracket=True)}')
        logger.error(msg)
        raise fastapi.HTTPException(status_code=502, detail=msg)
