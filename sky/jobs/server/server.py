"""REST API for managed jobs."""
import os

import fastapi
import httpx

from sky import sky_logging
from sky.jobs.server import core
from sky.jobs.server import dashboard_utils
from sky.server import common as server_common
from sky.server import stream_utils
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import requests as api_requests
from sky.skylet import constants
from sky.utils import common
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
        schedule_type=api_requests.ScheduleType.LONG,
        request_cluster_name=common.JOB_CONTROLLER_NAME,
    )


@router.post('/queue')
async def queue(request: fastapi.Request,
                jobs_queue_body: payloads.JobsQueueBody) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='jobs.queue',
        request_body=jobs_queue_body,
        func=core.queue,
        schedule_type=(api_requests.ScheduleType.LONG if jobs_queue_body.refresh
                       else api_requests.ScheduleType.SHORT),
        request_cluster_name=common.JOB_CONTROLLER_NAME,
    )


@router.post('/cancel')
async def cancel(request: fastapi.Request,
                 jobs_cancel_body: payloads.JobsCancelBody) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='jobs.cancel',
        request_body=jobs_cancel_body,
        func=core.cancel,
        schedule_type=api_requests.ScheduleType.SHORT,
        request_cluster_name=common.JOB_CONTROLLER_NAME,
    )


@router.post('/logs')
async def logs(
    request: fastapi.Request, jobs_logs_body: payloads.JobsLogsBody,
    background_tasks: fastapi.BackgroundTasks
) -> fastapi.responses.StreamingResponse:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='jobs.logs',
        request_body=jobs_logs_body,
        func=core.tail_logs,
        # TODO(aylei): We have tail logs scheduled as SHORT request, because it
        # should be responsive. However, it can be long running if the user's
        # job keeps running, and we should avoid it taking the SHORT worker
        # indefinitely.
        # When refresh is True we schedule it as LONG because a controller
        # restart might be needed.
        schedule_type=api_requests.ScheduleType.LONG
        if jobs_logs_body.refresh else api_requests.ScheduleType.SHORT,
        request_cluster_name=common.JOB_CONTROLLER_NAME,
    )
    request_task = api_requests.get_request(request.state.request_id)

    return stream_utils.stream_response(
        request_id=request_task.request_id,
        logs_path=request_task.log_path,
        background_tasks=background_tasks,
    )


@router.post('/download_logs')
async def download_logs(
        request: fastapi.Request,
        jobs_download_logs_body: payloads.JobsDownloadLogsBody) -> None:
    user_hash = jobs_download_logs_body.env_vars[constants.USER_ID_ENV_VAR]
    logs_dir_on_api_server = server_common.api_server_user_logs_dir_prefix(
        user_hash)
    logs_dir_on_api_server.expanduser().mkdir(parents=True, exist_ok=True)
    # We should reuse the original request body, so that the env vars, such as
    # user hash, are kept the same.
    jobs_download_logs_body.local_dir = str(logs_dir_on_api_server)
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='jobs.download_logs',
        request_body=jobs_download_logs_body,
        func=core.download_logs,
        schedule_type=api_requests.ScheduleType.LONG
        if jobs_download_logs_body.refresh else api_requests.ScheduleType.SHORT,
        request_cluster_name=common.JOB_CONTROLLER_NAME,
    )


@router.get('/dashboard')
async def dashboard(request: fastapi.Request,
                    user_hash: str) -> fastapi.Response:
    # TODO(cooperc): Support showing only jobs for a specific user.

    # FIX(zhwu/cooperc/eric): Fix log downloading (assumes global
    # /download_log/xx route)

    # Note: before #4717, each user had their own controller, and thus their own
    # dashboard. Now, all users share the same controller, so this isn't really
    # necessary. TODO(cooperc): clean up.

    # TODO: Put this in an executor to avoid blocking the main server thread.
    # It can take a long time if it needs to check the controller status.

    # Find the port for the dashboard of the user
    os.environ[constants.USER_ID_ENV_VAR] = user_hash
    server_common.reload_for_new_request(client_entrypoint=None,
                                         client_command=None,
                                         using_remote_api_server=False)
    logger.info(f'Starting dashboard for user hash: {user_hash}')

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
                                                    timeout=5)
                if response.is_success:
                    break  # Connection successful, proceed with the request
                # Raise an HTTPException here which will be caught by the
                # following except block to retry with new connection
                response.raise_for_status()
            except Exception as e:  # pylint: disable=broad-except
                # We catch all exceptions to gracefully handle unknown
                # errors and retry or raise an HTTPException to the client.
                # Assume an exception indicates that the dashboard connection
                # is stale - remove it so that a new one is created.
                dashboard_utils.remove_dashboard_session(user_hash)
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
