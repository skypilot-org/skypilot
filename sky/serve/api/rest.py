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
    executor.enqueue_request(
        request_id=request.state.request_id,
        request_name='serve/up',
        request_body=up_body,
        func=core.up,
    )


@router.post('/update')
async def update(
    request: fastapi.Request,
    update_body: payloads.ServeUpdateBody,
) -> None:
    executor.enqueue_request(
        request_id=request.state.request_id,
        request_name='serve/update',
        request_body=update_body,
        func=core.update,
    )


@router.post('/down')
async def down(
    request: fastapi.Request,
    down_body: payloads.ServeDownBody,
) -> None:
    executor.enqueue_request(
        request_id=request.state.request_id,
        request_name='serve/down',
        request_body=down_body,
        func=core.down,
    )


@router.get('/status')
async def status(
    request: fastapi.Request,
    status_body: payloads.ServeStatusBody,
) -> None:
    executor.enqueue_request(
        request_id=request.state.request_id,
        request_name='serve/status',
        request_body=status_body,
        func=core.status,
    )


@router.get('/logs')
async def tail_logs(
    request: fastapi.Request,
    log_body: payloads.ServeLogsBody,
) -> None:
    executor.enqueue_request(
        request_id=request.state.request_id,
        request_name='serve/logs',
        request_body=log_body,
        func=core.tail_logs,
    )
