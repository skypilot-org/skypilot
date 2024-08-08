"""REST API for SkyPilot."""
import argparse
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import time
from typing import List, Optional
import uuid
import zipfile

import colorama
import fastapi
import starlette.middleware.base

from sky import check as sky_check
from sky import core
from sky import execution
from sky import optimizer
from sky import sky_logging
from sky.api import common
from sky.api.requests import executor
from sky.api.requests import payloads
from sky.api.requests import tasks
from sky.clouds import service_catalog
from sky.jobs.api import rest as jobs_rest
from sky.serve.api import rest as serve_rest
from sky.utils import dag_utils
from sky.utils import registry
from sky.utils import subprocess_utils

# pylint: disable=ungrouped-imports
if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec

P = ParamSpec('P')

logger = sky_logging.init_logger(__name__)


class RequestIDMiddleware(starlette.middleware.base.BaseHTTPMiddleware):
    """Middleware to add a request ID to each request."""

    async def dispatch(self, request: fastapi.Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers['X-Request-ID'] = request_id
        return response


app = fastapi.FastAPI(prefix='/api/v1', debug=True)
app.add_middleware(RequestIDMiddleware)
app.include_router(jobs_rest.router, prefix='/jobs', tags=['jobs'])
app.include_router(serve_rest.router, prefix='/serve', tags=['serve'])


def refresh_cluster_status_event():
    """Periodically refresh the cluster status."""
    while True:
        print('Refreshing cluster status...')
        # TODO(zhwu): Periodically refresh will cause the cluster being locked
        # and other operations, such as down, may fail due to not being able to
        # acquire the lock.
        core.status(refresh=True)
        print('Refreshed cluster status...')
        time.sleep(20)


# Register the events to run in the background.
events = {'status': refresh_cluster_status_event}


@app.on_event('startup')
async def startup():
    for event_id, (event_name, event) in enumerate(events.items()):
        executor.start_background_request(request_id=str(event_id),
                                          request_name=event_name,
                                          request_body={},
                                          func=event)


@app.get('/check')
async def check(request: fastapi.Request, check_body: payloads.CheckBody):
    """Check enabled clouds."""
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='check',
        request_body=json.loads(check_body.model_dump_json()),
        func=sky_check.check,
        clouds=check_body.clouds,
        verbose=check_body.verbose,
    )


@app.get('/enabled_clouds')
async def enabled_clouds(request: fastapi.Request) -> None:
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='enabled_clouds',
        request_body={},
        func=core.enabled_clouds,
    )


@app.get('/realtime_gpu_availability')
async def realtime_gpu_availability(
    request: fastapi.Request,
    realtime_gpu_availability_body: payloads.RealtimeGpuAvailabilityRequestBody
) -> None:
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='realtime_gpu_availability',
        request_body=json.loads(
            realtime_gpu_availability_body.model_dump_json()),
        func=core.realtime_gpu_availability,
        name_filter=realtime_gpu_availability_body.name_filter,
        quantity_filter=realtime_gpu_availability_body.quantity_filter,
    )


@app.get('/list_accelerators')
async def list_accelerators(
        request: fastapi.Request,
        list_accelerator_counts_body: payloads.ListAcceleratorsBody) -> None:
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='list_accelerators',
        request_body=json.loads(list_accelerator_counts_body.model_dump_json()),
        func=service_catalog.list_accelerators,
        gpus_only=list_accelerator_counts_body.gpus_only,
        clouds=list_accelerator_counts_body.clouds,
        region_filter=list_accelerator_counts_body.region_filter,
        all_regions=list_accelerator_counts_body.all_regions,
        require_price=list_accelerator_counts_body.require_price,
        case_sensitive=list_accelerator_counts_body.case_sensitive,
    )


@app.get('/list_accelerator_counts')
async def list_accelerator_counts(
        request: fastapi.Request,
        list_accelerator_counts_body: payloads.ListAcceleratorsBody) -> None:
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='list_accelerator_counts',
        request_body=json.loads(list_accelerator_counts_body.model_dump_json()),
        func=service_catalog.list_accelerator_counts,
        gpus_only=list_accelerator_counts_body.gpus_only,
        clouds=list_accelerator_counts_body.clouds,
        region_filter=list_accelerator_counts_body.region_filter,
    )


@app.get('/optimize')
async def optimize(optimize_body: payloads.OptimizeBody,
                   request: fastapi.Request):
    with tempfile.NamedTemporaryFile(mode='w') as f:
        f.write(optimize_body.dag)
        f.flush()
        dag = dag_utils.load_chain_dag_from_yaml(f.name)
    request_id = request.state.request_id
    executor.start_background_request(
        request_id,
        request_name='optimize',
        request_body=json.loads(optimize_body.model_dump_json()),
        ignore_return_value=True,
        func=optimizer.Optimizer.optimize,
        dag=dag,
        minimize=optimize_body.minimize,
    )


@app.post('/upload')
async def upload_zip_file(user_hash: str,
                          file: fastapi.UploadFile = fastapi.File(...)):
    client_file_mounts_dir = (common.CLIENT_DIR.expanduser().resolve() /
                              user_hash / 'file_mounts')
    os.makedirs(client_file_mounts_dir, exist_ok=True)
    timestamp = str(int(time.time()))
    try:
        # Save the uploaded zip file temporarily
        zip_file_path = client_file_mounts_dir / f'{timestamp}.zip'
        with open(zip_file_path, 'wb') as f:
            contents = await file.read()
            f.write(contents)

        with zipfile.ZipFile(zip_file_path, 'r') as zipf:
            for member in zipf.namelist():
                # Determine the new path
                filename = os.path.basename(member)
                original_path = os.path.normpath(member)
                new_path = client_file_mounts_dir / original_path.lstrip('/')

                if not filename:  # This is for directories, skip
                    new_path.mkdir(parents=True, exist_ok=True)
                    continue
                with zipf.open(member) as member_file:
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    new_path.write_bytes(member_file.read())

        # Cleanup the temporary file
        zip_file_path.unlink()

        return {'status': 'files uploaded and extracted'}
    except Exception as e:  # pylint: disable=broad-except
        return {'detail': str(e)}


@app.post('/launch')
async def launch(launch_body: payloads.LaunchBody, request: fastapi.Request):
    """Launch a task.

    Args:
        task: The YAML string of the task to launch.
    """
    dag = common.process_mounts_in_task(launch_body.task,
                                        launch_body.env_vars,
                                        launch_body.cluster_name,
                                        workdir_only=False)

    backend = registry.BACKEND_REGISTRY.from_str(launch_body.backend)
    request_id = request.state.request_id
    executor.start_background_request(
        request_id,
        request_name='launch',
        request_body=json.loads(launch_body.model_dump_json()),
        func=execution.launch,
        task=dag,
        cluster_name=launch_body.cluster_name,
        retry_until_up=launch_body.retry_until_up,
        idle_minutes_to_autostop=launch_body.idle_minutes_to_autostop,
        dryrun=launch_body.dryrun,
        down=launch_body.down,
        backend=backend,
        optimize_target=launch_body.optimize_target,
        detach_setup=launch_body.detach_setup,
        detach_run=launch_body.detach_run,
        no_setup=launch_body.no_setup,
        clone_disk_from=launch_body.clone_disk_from,
        _quiet_optimizer=launch_body.quiet_optimizer,
        _is_launched_by_jobs_controller=launch_body.
        is_launched_by_jobs_controller,
        _is_launched_by_sky_serve_controller=launch_body.
        is_launched_by_sky_serve_controller,
        _disable_controller_check=launch_body.disable_controller_check,
    )


@app.post('/exec')
# pylint: disable=redefined-builtin
async def exec(request: fastapi.Request, exec_body: payloads.ExecBody):
    dag = common.process_mounts_in_task(exec_body.task,
                                        exec_body.env_vars,
                                        exec_body.cluster_name,
                                        workdir_only=True)
    if len(dag.tasks) != 1:
        raise fastapi.HTTPException(
            status_code=400,
            detail='The DAG for exec must have exactly one task.')
    task = dag.tasks[0]
    backend = registry.BACKEND_REGISTRY.from_str(exec_body.backend)

    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='exec',
        request_body=json.loads(exec_body.model_dump_json()),
        func=execution.exec,
        task=task,
        cluster_name=exec_body.cluster_name,
        backend=backend,
        dryrun=exec_body.dryrun,
        down=exec_body.down,
        detach_run=exec_body.detach_run,
    )


@app.post('/stop')
async def stop(request: fastapi.Request, stop_body: payloads.StopOrDownBody):
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='stop',
        request_body=json.loads(stop_body.model_dump_json()),
        func=core.stop,
        cluster_name=stop_body.cluster_name,
        purge=stop_body.purge,
    )


@app.get('/status')
async def status(
    request: fastapi.Request,
    status_body: payloads.StatusBody = payloads.StatusBody()
) -> None:
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='status',
        request_body=json.loads(status_body.model_dump_json()),
        func=core.status,
        cluster_names=status_body.cluster_names,
        refresh=status_body.refresh,
    )


@app.get('/endpoints')
async def endpoints(request: fastapi.Request,
                    endpoint_body: payloads.EndpointBody) -> None:
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='endpoints',
        request_body=json.loads(endpoint_body.model_dump_json()),
        func=core.endpoints,
        cluster_name=endpoint_body.cluster_name,
        port=endpoint_body.port,
    )


@app.post('/down')
async def down(request: fastapi.Request, down_body: payloads.StopOrDownBody):
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='down',
        request_body=json.loads(down_body.model_dump_json()),
        func=core.down,
        cluster_name=down_body.cluster_name,
        purge=down_body.purge,
    )


@app.post('/start')
async def start(request: fastapi.Request, start_body: payloads.StartBody):
    """Restart a cluster."""
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='start',
        request_body=json.loads(start_body.model_dump_json()),
        func=core.start,
        cluster_name=start_body.cluster_name,
        idle_minutes_to_autostop=start_body.idle_minutes_to_autostop,
        retry_until_up=start_body.retry_until_up,
        down=start_body.down,
        force=start_body.force,
    )


@app.post('/autostop')
async def autostop(request: fastapi.Request,
                   autostop_body: payloads.AutostopBody):
    """Set the autostop time for a cluster."""
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='autostop',
        request_body=json.loads(autostop_body.model_dump_json()),
        func=core.autostop,
        cluster_name=autostop_body.cluster_name,
        idle_minutes=autostop_body.idle_minutes,
        down=autostop_body.down,
    )


@app.get('/queue')
async def queue(request: fastapi.Request, queue_body: payloads.QueueBody):
    """Get the queue of tasks for a cluster."""
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='queue',
        request_body=json.loads(queue_body.model_dump_json()),
        func=core.queue,
        cluster_name=queue_body.cluster_name,
        skip_finished=queue_body.skip_finished,
        all_users=queue_body.all_users,
    )


@app.get('/job_status')
async def job_status(request: fastapi.Request,
                     job_status_body: payloads.JobStatusBody):
    """Get the status of a job."""
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='job_status',
        request_body=json.loads(job_status_body.model_dump_json()),
        func=core.job_status,
        cluster_name=job_status_body.cluster_name,
        job_ids=job_status_body.job_ids,
    )


@app.post('/cancel')
async def cancel(request: fastapi.Request,
                 cancel_body: payloads.CancelBody) -> None:
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='cancel',
        request_body=json.loads(cancel_body.model_dump_json()),
        func=core.cancel,
        cluster_name=cancel_body.cluster_name,
        job_ids=cancel_body.job_ids,
        all=cancel_body.all,
    )


@app.get('/logs')
async def logs(request: fastapi.Request,
               cluster_job_body: payloads.ClusterJobBody) -> None:
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='logs',
        request_body=json.loads(cluster_job_body.model_dump_json()),
        func=core.tail_logs,
        cluster_name=cluster_job_body.cluster_name,
        job_id=cluster_job_body.job_id,
        follow=cluster_job_body.follow,
    )


# TODO(zhwu): expose download_logs
# @app.get('/download_logs')
# async def download_logs(request: fastapi.Request,
#                         cluster_jobs_body: payloads.ClusterJobsBody,
# ) -> Dict[str, str]:
#     """Download logs to API server and returns the job id to log dir
#     mapping."""
#     # Call the function directly to download the logs to the API server first.
#     log_dirs = core.download_logs(cluster_name=cluster_jobs_body.cluster_name,
#                        job_ids=cluster_jobs_body.job_ids)

#     return log_dirs


@app.get('/cost_report')
async def cost_report(request: fastapi.Request) -> None:
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='cost_report',
        request_body={},
        func=core.cost_report,
    )


@app.get('/storage/ls')
async def storage_ls(request: fastapi.Request):
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='storage_ls',
        request_body={},
        func=core.storage_ls,
    )


@app.post('/storage/delete')
async def storage_delete(request: fastapi.Request,
                         storage_body: payloads.StorageBody):
    executor.start_background_request(
        request_id=request.state.request_id,
        request_name='storage_delete',
        request_body=json.loads(storage_body.model_dump_json()),
        func=core.storage_delete,
        name=storage_body.name,
    )


@app.get('/get')
async def get(get_body: payloads.RequestIdBody) -> tasks.RequestTaskPayload:
    while True:
        request_task = tasks.get_request(get_body.request_id)
        if request_task is None:
            print(f'No task with request ID {get_body.request_id}')
            raise fastapi.HTTPException(
                status_code=404,
                detail=f'Request {get_body.request_id} not found')
        if request_task.status > tasks.RequestStatus.RUNNING:
            return request_task.encode()
        await asyncio.sleep(1)


async def log_streamer(request_id: str, log_path: pathlib.Path):
    with log_path.open('rb') as f:
        while True:
            line = f.readline()
            if not line:
                request_task = tasks.get_request(request_id)
                if request_task.status > tasks.RequestStatus.RUNNING:
                    break
                await asyncio.sleep(1)
                continue
            yield line


@app.get('/stream')
async def stream(
        stream_body: payloads.RequestIdBody
) -> fastapi.responses.StreamingResponse:
    request_id = stream_body.request_id
    request_task = tasks.get_request(request_id)
    if request_task is None:
        print(f'No task with request ID {request_id}')
        raise fastapi.HTTPException(status_code=404,
                                    detail=f'Request {request_id} not found')
    log_path = request_task.log_path
    return fastapi.responses.StreamingResponse(log_streamer(
        request_id, log_path),
                                               media_type='text/plain')


@app.post('/abort')
async def abort(abort_body: payloads.RequestIdBody):
    print(f'Trying to kill request ID {abort_body.request_id}')
    with tasks.update_rest_task(abort_body.request_id) as rest_task:
        if rest_task is None:
            print(f'No task with request ID {abort_body.request_id}')
            raise fastapi.HTTPException(
                status_code=404,
                detail=f'Request {abort_body.request_id} not found')
        if rest_task.status > tasks.RequestStatus.RUNNING:
            print(f'Request {abort_body.request_id} already finished')
            return
        rest_task.status = tasks.RequestStatus.ABORTED
        if rest_task.pid is not None:
            subprocess_utils.kill_children_processes(
                parent_pids=[rest_task.pid])
    print(f'Killed request: {abort_body.request_id}')


@app.get('/requests')
async def requests(request_id: Optional[str] = None) -> List[tasks.RequestTask]:
    if request_id is None:
        return tasks.get_request_tasks()
    else:
        request_task = tasks.get_request(request_id)
        if request_task is None:
            raise fastapi.HTTPException(
                status_code=404, detail=f'Request {request_id} not found')
        return [request_task]


@app.get('/health', response_class=fastapi.responses.PlainTextResponse)
async def health() -> str:
    return (f'SkyPilot API Server: {colorama.Style.BRIGHT}{colorama.Fore.GREEN}'
            f'Healthy{colorama.Style.RESET_ALL}\n')


# @app.get('/version', response_class=fastapi.responses.PlainTextResponse)

app.include_router(core.app_router)

if __name__ == '__main__':
    import uvicorn
    tasks.reset_db()

    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', default=8000, type=int)
    parser.add_argument('--reload', action='store_true')
    cmd_args = parser.parse_args()
    uvicorn.run('sky.api.rest:app',
                host=cmd_args.host,
                port=cmd_args.port,
                reload=cmd_args.reload)
