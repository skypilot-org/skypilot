"""Rest APIs for SkyServe."""

import fastapi

from sky.api.requests import executor
from sky.api.requests import payloads
from sky.serve.api import core

router = fastapi.APIRouter()


@router.post('/up')
async def up(
    request: fastapi.Request,
    up_body: payloads.ServeUpBody,
) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='serve/up',
        request_body=up_body,
        func=core.up,
        schedule_type=executor.ScheduleType.BLOCKING,
    )


@router.post('/update')
async def update(
    request: fastapi.Request,
    update_body: payloads.ServeUpdateBody,
) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='serve/update',
        request_body=update_body,
        func=core.update,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@router.post('/down')
async def down(
    request: fastapi.Request,
    down_body: payloads.ServeDownBody,
) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='serve/down',
        request_body=down_body,
        func=core.down,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@router.post('/terminate-replica')
async def terminate_replica(
    request: fastapi.Request,
    terminate_replica_body: payloads.ServeTerminateReplicaBody,
) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='serve/terminate-replica',
        request_body=terminate_replica_body,
        func=core.terminate_replica,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@router.get('/status')
async def status(
    request: fastapi.Request,
    status_body: payloads.ServeStatusBody,
) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='serve/status',
        request_body=status_body,
        func=core.status,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@router.get('/logs')
async def tail_logs(
    request: fastapi.Request,
    log_body: payloads.ServeLogsBody,
) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='serve/logs',
        request_body=log_body,
        func=core.tail_logs,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )
