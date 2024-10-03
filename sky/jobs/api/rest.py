"""REST API for managed jobs."""

import fastapi

from sky.api.requests import executor
from sky.api.requests import payloads
from sky.jobs.api import core

router = fastapi.APIRouter()


@router.post('/launch')
async def launch(request: fastapi.Request,
                 jobs_launch_body: payloads.JobsLaunchBody) -> None:

    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='jobs/launch',
        request_body=jobs_launch_body,
        func=core.launch,
        schedule_type=executor.ScheduleType.BLOCKING,
    )


@router.get('/queue')
async def queue(request: fastapi.Request,
                jobs_queue_body: payloads.JobsQueueBody) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='jobs/queue',
        request_body=jobs_queue_body,
        func=core.queue,
        schedule_type=(executor.ScheduleType.BLOCKING if jobs_queue_body.refresh
                       else executor.ScheduleType.NON_BLOCKING),
    )


@router.post('/cancel')
async def cancel(request: fastapi.Request,
                 jobs_cancel_body: payloads.JobsCancelBody) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='jobs/cancel',
        request_body=jobs_cancel_body,
        func=core.cancel,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@router.get('/logs')
async def logs(request: fastapi.Request,
               jobs_logs_body: payloads.JobsLogsBody) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='jobs/logs',
        request_body=jobs_logs_body,
        func=core.tail_logs,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )
