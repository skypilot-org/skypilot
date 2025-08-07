"""REST API for managed jobs."""

import pathlib

import fastapi

from sky import sky_logging
from sky.jobs.server import core
from sky.server import common as server_common
from sky.server import stream_utils
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import requests as api_requests
from sky.skylet import constants
from sky.utils import common

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


@router.post('/pool_apply')
async def pool_apply(request: fastapi.Request,
                     jobs_pool_apply_body: payloads.JobsPoolApplyBody) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='jobs.pool_apply',
        request_body=jobs_pool_apply_body,
        func=core.pool_apply,
        schedule_type=api_requests.ScheduleType.LONG,
        request_cluster_name=common.JOB_CONTROLLER_NAME,
    )


@router.post('/pool_down')
async def pool_down(request: fastapi.Request,
                    jobs_pool_down_body: payloads.JobsPoolDownBody) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='jobs.pool_down',
        request_body=jobs_pool_down_body,
        func=core.pool_down,
        schedule_type=api_requests.ScheduleType.SHORT,
        request_cluster_name=common.JOB_CONTROLLER_NAME,
    )


@router.post('/pool_status')
async def pool_status(
        request: fastapi.Request,
        jobs_pool_status_body: payloads.JobsPoolStatusBody) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='jobs.pool_status',
        request_body=jobs_pool_status_body,
        func=core.pool_status,
        schedule_type=api_requests.ScheduleType.SHORT,
        request_cluster_name=common.JOB_CONTROLLER_NAME,
    )


@router.post('/pool_logs')
async def pool_tail_logs(
    request: fastapi.Request, log_body: payloads.JobsPoolLogsBody,
    background_tasks: fastapi.BackgroundTasks
) -> fastapi.responses.StreamingResponse:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='jobs.pool_logs',
        request_body=log_body,
        func=core.pool_tail_logs,
        schedule_type=api_requests.ScheduleType.SHORT,
        request_cluster_name=common.JOB_CONTROLLER_NAME,
    )

    request_task = api_requests.get_request(request.state.request_id)

    return stream_utils.stream_response(
        request_id=request_task.request_id,
        logs_path=request_task.log_path,
        background_tasks=background_tasks,
    )


@router.post('/pool_sync-down-logs')
async def pool_download_logs(
    request: fastapi.Request,
    download_logs_body: payloads.JobsPoolDownloadLogsBody,
) -> None:
    user_hash = download_logs_body.env_vars[constants.USER_ID_ENV_VAR]
    timestamp = sky_logging.get_run_timestamp()
    logs_dir_on_api_server = (
        pathlib.Path(server_common.api_server_user_logs_dir_prefix(user_hash)) /
        'pool' / f'{download_logs_body.pool_name}_{timestamp}')
    logs_dir_on_api_server.mkdir(parents=True, exist_ok=True)
    # We should reuse the original request body, so that the env vars, such as
    # user hash, are kept the same.
    download_logs_body.local_dir = str(logs_dir_on_api_server)
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='jobs.pool_sync_down_logs',
        request_body=download_logs_body,
        func=core.pool_sync_down_logs,
        schedule_type=api_requests.ScheduleType.SHORT,
        request_cluster_name=common.JOB_CONTROLLER_NAME,
    )
