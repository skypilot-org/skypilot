"""Rest APIs for SkyServe."""

import json

import fastapi

from sky.api import common
from sky.api.requests import executor
from sky.api.requests import payloads
from sky.serve.api import core

router = fastapi.APIRouter()


@router.post('/up')
async def up(
    request: fastapi.Request,
    up_body: payloads.ServeUpBody,
) -> None:
    dag = common.process_mounts_in_task(up_body.task,
                                        up_body.env_vars,
                                        up_body.service_name,
                                        workdir_only=False)
    assert len(dag.tasks) == 1, ('Must only specify one task in the DAG for '
                                 'a service.', dag)
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='serve/up',
        request_body=json.loads(up_body.model_dump_json()),
        func=core.up,
        task=dag.tasks[0],
        service_name=up_body.service_name,
    )


@router.post('/update')
async def update(
    request: fastapi.Request,
    update_body: payloads.ServeUpdateBody,
) -> None:
    dag = common.process_mounts_in_task(update_body.task,
                                        update_body.env_vars,
                                        update_body.service_name,
                                        workdir_only=False)
    assert len(dag.tasks) == 1, ('Must only specify one task in the DAG for '
                                 'a service.', dag)
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='serve/update',
        request_body=json.loads(update_body.model_dump_json()),
        func=core.update,
        task=dag.tasks[0],
        service_name=update_body.service_name,
        mode=update_body.mode,
    )


@router.post('/down')
async def down(
    request: fastapi.Request,
    down_body: payloads.ServeDownBody,
) -> None:
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='serve/down',
        request_body=json.loads(down_body.model_dump_json()),
        func=core.down,
        service_names=down_body.service_names,
        all=down_body.all,
        purge=down_body.purge,
    )


@router.get('/status')
async def status(
    request: fastapi.Request,
    status_body: payloads.ServeStatusBody,
) -> None:
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='serve/status',
        request_body=json.loads(status_body.model_dump_json()),
        func=core.status,
        service_names=status_body.service_names,
    )


@router.get('/logs')
async def tail_logs(
    request: fastapi.Request,
    log_body: payloads.ServeLogsBody,
) -> None:
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='serve/logs',
        request_body=json.loads(log_body.model_dump_json()),
        func=core.tail_logs,
        service_name=log_body.service_name,
        target=log_body.target,
        replica_id=log_body.replica_id,
        follow=log_body.follow,
    )
