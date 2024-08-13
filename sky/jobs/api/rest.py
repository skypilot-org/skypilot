"""REST API for managed jobs."""
import json

import fastapi

from sky.api import common
from sky.api.requests import executor
from sky.api.requests import payloads
from sky.jobs.api import core

router = fastapi.APIRouter()


@router.post('/launch')
async def launch(request: fastapi.Request,
                 jobs_launch_body: payloads.JobsLaunchBody) -> None:
    dag = common.process_mounts_in_task(jobs_launch_body.task,
                                        jobs_launch_body.env_vars,
                                        workdir_only=False)

    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='jobs/launch',
        request_body=json.loads(jobs_launch_body.model_dump_json()),
        func=core.launch,
        task=dag,
        name=jobs_launch_body.name,
        detach_run=jobs_launch_body.detach_run,
        retry_until_up=jobs_launch_body.retry_until_up,
    )


@router.get('/queue')
async def queue(request: fastapi.Request,
                jobs_queue_body: payloads.JobsQueueBody) -> None:
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='jobs/queue',
        request_body=json.loads(jobs_queue_body.model_dump_json()),
        func=core.queue,
        refresh=jobs_queue_body.refresh,
        skip_finished=jobs_queue_body.skip_finished,
    )


@router.post('/cancel')
async def cancel(request: fastapi.Request,
                 jobs_cancel_body: payloads.JobsCancelBody) -> None:
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='jobs/cancel',
        request_body=json.loads(jobs_cancel_body.model_dump_json()),
        func=core.cancel,
        name=jobs_cancel_body.name,
        job_ids=jobs_cancel_body.job_ids,
        all=jobs_cancel_body.all,
    )


@router.get('/logs')
async def logs(request: fastapi.Request,
               jobs_logs_body: payloads.JobsLogsBody) -> None:
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='jobs/logs',
        request_body=json.loads(jobs_logs_body.model_dump_json()),
        func=core.tail_logs,
        name=jobs_logs_body.name,
        job_id=jobs_logs_body.job_id,
        follow=jobs_logs_body.follow,
        controller=jobs_logs_body.controller,
    )
