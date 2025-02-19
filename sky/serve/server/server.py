"""Rest APIs for SkyServe."""

import fastapi

from sky import sky_logging
from sky.serve.server import core
from sky.server import stream_utils
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import requests as api_requests
from sky.utils import common

logger = sky_logging.init_logger(__name__)
router = fastapi.APIRouter()


@router.post('/up')
async def up(
    request: fastapi.Request,
    up_body: payloads.ServeUpBody,
) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='serve.up',
        request_body=up_body,
        func=core.up,
        schedule_type=api_requests.ScheduleType.LONG,
        request_cluster_name=common.SKY_SERVE_CONTROLLER_NAME,
    )


@router.post('/update')
async def update(
    request: fastapi.Request,
    update_body: payloads.ServeUpdateBody,
) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='serve.update',
        request_body=update_body,
        func=core.update,
        schedule_type=api_requests.ScheduleType.SHORT,
        request_cluster_name=common.SKY_SERVE_CONTROLLER_NAME,
    )


@router.post('/down')
async def down(
    request: fastapi.Request,
    down_body: payloads.ServeDownBody,
) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='serve.down',
        request_body=down_body,
        func=core.down,
        schedule_type=api_requests.ScheduleType.SHORT,
        request_cluster_name=common.SKY_SERVE_CONTROLLER_NAME,
    )


@router.post('/terminate-replica')
async def terminate_replica(
    request: fastapi.Request,
    terminate_replica_body: payloads.ServeTerminateReplicaBody,
) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='serve.terminate_replica',
        request_body=terminate_replica_body,
        func=core.terminate_replica,
        schedule_type=api_requests.ScheduleType.SHORT,
        request_cluster_name=common.SKY_SERVE_CONTROLLER_NAME,
    )


@router.post('/status')
async def status(
    request: fastapi.Request,
    status_body: payloads.ServeStatusBody,
) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='serve.status',
        request_body=status_body,
        func=core.status,
        schedule_type=api_requests.ScheduleType.SHORT,
        request_cluster_name=common.SKY_SERVE_CONTROLLER_NAME,
    )


@router.post('/logs')
async def tail_logs(
    request: fastapi.Request, log_body: payloads.ServeLogsBody,
    background_tasks: fastapi.BackgroundTasks
) -> fastapi.responses.StreamingResponse:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='serve.logs',
        request_body=log_body,
        func=core.tail_logs,
        schedule_type=api_requests.ScheduleType.SHORT,
        request_cluster_name=common.SKY_SERVE_CONTROLLER_NAME,
    )

    request_task = api_requests.get_request(request.state.request_id)

    return stream_utils.stream_response(
        request_id=request_task.request_id,
        logs_path=request_task.log_path,
        background_tasks=background_tasks,
    )
