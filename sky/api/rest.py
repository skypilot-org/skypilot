"""REST API for SkyPilot."""

import argparse
import asyncio
import contextlib
import os
import pathlib
import sys
import time
from typing import List, Optional
import uuid
import zipfile

import colorama
import fastapi
from fastapi.middleware import cors
import starlette.middleware.base

from sky import check as sky_check
from sky import clouds
from sky import core
from sky import execution
from sky import optimizer
from sky import sky_logging
from sky.api import common
from sky.api.requests import executor
from sky.api.requests import payloads
from sky.api.requests import requests as requests_lib
from sky.clouds import service_catalog
from sky.jobs.api import rest as jobs_rest
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.serve.api import rest as serve_rest
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import message_utils
from sky.utils import rich_utils
from sky.utils import status_lib

# pylint: disable=ungrouped-imports
if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec

P = ParamSpec('P')

logger = sky_logging.init_logger(__name__)

# TODO(zhwu): Streaming requests, such log tailing after sky launch or sky logs,
# need to be detached from the main requests queue. Otherwise, the streaming
# response will block other requests from being processed.


class RequestIDMiddleware(starlette.middleware.base.BaseHTTPMiddleware):
    """Middleware to add a request ID to each request."""

    async def dispatch(self, request: fastapi.Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers['X-Request-ID'] = request_id
        return response


def refresh_cluster_status_event():
    """Periodically refresh the cluster status."""
    while True:
        print('Refreshing cluster status...')
        # TODO(zhwu): Periodically refresh will cause the cluster being locked
        # and other operations, such as down, may fail due to not being able to
        # acquire the lock.
        core.status(refresh=True, all_users=True)
        print('Refreshed cluster status...')
        time.sleep(20)


# Register the events to run in the background.
events = {'status': refresh_cluster_status_event}


@contextlib.asynccontextmanager
async def lifespan(app: fastapi.FastAPI):  # pylint: disable=redefined-outer-name
    """FastAPI lifespan context manager."""
    del app  # unused
    # Startup: Run background tasks
    for event_id, (event_name, event) in enumerate(events.items()):
        executor.schedule_request(
            request_id=str(event_id),
            request_name=event_name,
            request_body=payloads.RequestBody(),
            func=event,
            schedule_type=executor.ScheduleType.NON_BLOCKING)
    yield
    # Shutdown: Add any cleanup code here if needed


app = fastapi.FastAPI(prefix='/api/v1', debug=True, lifespan=lifespan)
app.add_middleware(
    cors.CORSMiddleware,
    allow_origins=['*'],  # Specify the correct domains for production
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
    expose_headers=['X-Request-ID'])
app.add_middleware(RequestIDMiddleware)
app.include_router(jobs_rest.router, prefix='/jobs', tags=['jobs'])
app.include_router(serve_rest.router, prefix='/serve', tags=['serve'])


@app.post('/check')
async def check(request: fastapi.Request, check_body: payloads.CheckBody):
    """Check enabled clouds."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='check',
        request_body=check_body,
        func=sky_check.check,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@app.get('/enabled_clouds')
async def enabled_clouds(request: fastapi.Request) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='enabled_clouds',
        request_body=payloads.RequestBody(),
        func=core.enabled_clouds,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@app.post('/realtime_gpu_availability')
async def realtime_gpu_availability(
    request: fastapi.Request,
    realtime_gpu_availability_body: payloads.RealtimeGpuAvailabilityRequestBody
) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='realtime_gpu_availability',
        request_body=realtime_gpu_availability_body,
        func=core.realtime_gpu_availability,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@app.get('/status_kubernetes')
async def status_kubernetes(request: fastapi.Request):
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='status_kubernetes',
        request_body=payloads.RequestBody(),
        func=core.status_kubernetes,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@app.post('/list_accelerators')
async def list_accelerators(
        request: fastapi.Request,
        list_accelerator_counts_body: payloads.ListAcceleratorsBody) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='list_accelerators',
        request_body=list_accelerator_counts_body,
        func=service_catalog.list_accelerators,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@app.post('/list_accelerator_counts')
async def list_accelerator_counts(
        request: fastapi.Request,
        list_accelerator_counts_body: payloads.ListAcceleratorCountsBody
) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='list_accelerator_counts',
        request_body=list_accelerator_counts_body,
        func=service_catalog.list_accelerator_counts,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@app.post('/optimize')
async def optimize(optimize_body: payloads.OptimizeBody,
                   request: fastapi.Request):
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='optimize',
        request_body=optimize_body,
        ignore_return_value=True,
        func=optimizer.Optimizer.optimize,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
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


@app.post('/download')
async def download(download_body: payloads.DownloadBody):
    """Download a folder from the cluster to the local machine."""
    folder_paths = [
        pathlib.Path(folder_path) for folder_path in download_body.folder_paths
    ]
    user_hash = download_body.env_vars[constants.USER_ID_ENV_VAR]
    logs_dir_on_api_server = common.api_server_logs_dir_prefix(user_hash)
    for folder_path in folder_paths:
        if not str(folder_path).startswith(str(logs_dir_on_api_server)):
            raise fastapi.HTTPException(
                status_code=400, detail=f'Invalid folder path: {folder_path}')

        if not folder_path.exists():
            raise fastapi.HTTPException(
                status_code=404, detail=f'Folder not found: {folder_path}')

    # Create a temporary zip file
    zip_filename = f'folder_{int(time.time())}.zip'
    zip_path = pathlib.Path(common.api_server_logs_dir_prefix(
        user_hash)).expanduser().resolve() / zip_filename

    try:
        folders = [
            str(folder_path.expanduser().resolve())
            for folder_path in folder_paths
        ]
        common_utils.zip_files_and_folders(folders, zip_path)

        # Add home path to the response headers, so that the client can replace
        # the remote path in the zip file to the local path.
        headers = {
            'Content-Disposition': f'attachment; filename="{zip_filename}"',
            'X-Home-Path': str(pathlib.Path.home())
        }

        # Return the zip file as a download
        return fastapi.responses.FileResponse(
            path=zip_path,
            filename=zip_filename,
            media_type='application/zip',
            headers=headers,
            background=fastapi.BackgroundTasks().add_task(
                lambda: zip_path.unlink(missing_ok=True)))
    except Exception as e:
        raise fastapi.HTTPException(status_code=500,
                                    detail=f'Error creating zip file: {str(e)}')


@app.post('/launch')
async def launch(launch_body: payloads.LaunchBody, request: fastapi.Request):
    """Launch a task.

    Args:
        task: The YAML string of the task to launch.
    """
    request_id = request.state.request_id
    executor.schedule_request(
        request_id,
        request_name='launch',
        request_body=launch_body,
        func=execution.launch,
        schedule_type=executor.ScheduleType.BLOCKING,
    )


@app.post('/exec')
# pylint: disable=redefined-builtin
async def exec(request: fastapi.Request, exec_body: payloads.ExecBody):

    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='exec',
        request_body=exec_body,
        func=execution.exec,
        schedule_type=executor.ScheduleType.BLOCKING,
    )


@app.post('/stop')
async def stop(request: fastapi.Request, stop_body: payloads.StopOrDownBody):
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='stop',
        request_body=stop_body,
        func=core.stop,
        schedule_type=executor.ScheduleType.BLOCKING,
    )


@app.post('/status')
async def status(
    request: fastapi.Request,
    status_body: payloads.StatusBody = payloads.StatusBody()
) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='status',
        request_body=status_body,
        func=core.status,
        schedule_type=(executor.ScheduleType.BLOCKING if status_body.refresh
                       else executor.ScheduleType.NON_BLOCKING),
    )


@app.post('/endpoints')
async def endpoints(request: fastapi.Request,
                    endpoint_body: payloads.EndpointBody) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='endpoints',
        request_body=endpoint_body,
        func=core.endpoints,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@app.post('/down')
async def down(request: fastapi.Request, down_body: payloads.StopOrDownBody):
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='down',
        request_body=down_body,
        func=core.down,
        schedule_type=executor.ScheduleType.BLOCKING,
    )


@app.post('/start')
async def start(request: fastapi.Request, start_body: payloads.StartBody):
    """Restart a cluster."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='start',
        request_body=start_body,
        func=core.start,
        schedule_type=executor.ScheduleType.BLOCKING,
    )


@app.post('/autostop')
async def autostop(request: fastapi.Request,
                   autostop_body: payloads.AutostopBody):
    """Set the autostop time for a cluster."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='autostop',
        request_body=autostop_body,
        func=core.autostop,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@app.post('/queue')
async def queue(request: fastapi.Request, queue_body: payloads.QueueBody):
    """Get the queue of tasks for a cluster."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='queue',
        request_body=queue_body,
        func=core.queue,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@app.post('/job_status')
async def job_status(request: fastapi.Request,
                     job_status_body: payloads.JobStatusBody):
    """Get the status of a job."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='job_status',
        request_body=job_status_body,
        func=core.job_status,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@app.post('/cancel')
async def cancel(request: fastapi.Request,
                 cancel_body: payloads.CancelBody) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='cancel',
        request_body=cancel_body,
        func=core.cancel,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@app.post('/logs')
async def logs(request: fastapi.Request,
               cluster_job_body: payloads.ClusterJobBody) -> None:
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='logs',
        request_body=cluster_job_body,
        func=core.tail_logs,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@app.post('/download_logs')
async def download_logs(request: fastapi.Request,
                        cluster_jobs_body: payloads.ClusterJobsBody) -> None:

    user_hash = cluster_jobs_body.env_vars[constants.USER_ID_ENV_VAR]
    logs_dir_on_api_server = common.api_server_logs_dir_prefix(user_hash)
    logs_dir_on_api_server.expanduser().mkdir(parents=True, exist_ok=True)
    cluster_job_download_logs_body = payloads.ClusterJobsDownloadLogsBody(
        cluster_name=cluster_jobs_body.cluster_name,
        job_ids=cluster_jobs_body.job_ids,
        local_dir=str(logs_dir_on_api_server),
    )
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='download_logs',
        request_body=cluster_job_download_logs_body,
        func=core.download_logs,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
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
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='cost_report',
        request_body=payloads.RequestBody(),
        func=core.cost_report,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@app.get('/storage/ls')
async def storage_ls(request: fastapi.Request):
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='storage_ls',
        request_body=payloads.RequestBody(),
        func=core.storage_ls,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@app.post('/storage/delete')
async def storage_delete(request: fastapi.Request,
                         storage_body: payloads.StorageBody):
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='storage_delete',
        request_body=storage_body,
        func=core.storage_delete,
        schedule_type=executor.ScheduleType.BLOCKING,
    )


@app.post('/local_up')
async def local_up(request: fastapi.Request,
                   local_up_body: payloads.LocalUpBody):
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='local_up',
        request_body=local_up_body,
        func=core.local_up,
        schedule_type=executor.ScheduleType.BLOCKING,
    )


@app.post('/local_down')
async def local_down(request: fastapi.Request):
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='local_down',
        request_body=payloads.RequestBody(),
        func=core.local_down,
        schedule_type=executor.ScheduleType.BLOCKING,
    )


# TODO(zhwu): remove this after debugging
def long_running_request_inner():
    while True:
        print('long_running_request is running ...')
        time.sleep(5)


@app.get('/long_running_request')
async def long_running_request(request: fastapi.Request):
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='long_running_request',
        request_body=payloads.RequestBody(),
        func=long_running_request_inner,
        schedule_type=executor.ScheduleType.BLOCKING,
    )


@app.get('/get')
async def get(request_id: str) -> requests_lib.RequestPayload:
    while True:
        request_task = requests_lib.get_request(request_id)
        if request_task is None:
            print(f'No task with request ID {request_id}', flush=True)
            raise fastapi.HTTPException(
                status_code=404, detail=f'Request {request_id} not found')
        if request_task.status > requests_lib.RequestStatus.RUNNING:
            return request_task.encode()
        await asyncio.sleep(1)


async def log_streamer(request_id: Optional[str],
                       log_path: pathlib.Path,
                       plain_logs: bool = False):
    if request_id is not None:
        request_task = requests_lib.get_request(request_id)
        rich_status = rich_utils.safe_status(f'Checking request: {request_id}')
        if not plain_logs:
            rich_status.start()
        while request_task.status < requests_lib.RequestStatus.RUNNING:
            rich_status.update(f'Waiting for request to start: {request_id}')
            await asyncio.sleep(1)
            request_task = requests_lib.get_request(request_id)
        if not plain_logs:
            rich_status.stop()

    with log_path.open('rb') as f:
        while True:
            line = f.readline()
            if not line:
                if request_id is not None:
                    request_task = requests_lib.get_request(request_id)
                    if request_task.status > requests_lib.RequestStatus.RUNNING:
                        break
                await asyncio.sleep(1)
                continue
            line_str = line.decode('utf-8')
            if plain_logs:
                is_payload, line_str = message_utils.decode_payload(
                    line_str, raise_for_mismatch=False)
                if is_payload:
                    await asyncio.sleep(0)  # Allow other tasks to run
                    continue
                line_str = common_utils.remove_color(line_str)
            yield line_str
            await asyncio.sleep(0)  # Allow other tasks to run


@app.get('/stream')
async def stream(
        request_id: Optional[str] = None,
        log_path: Optional[str] = None,
        plain_logs: bool = True) -> fastapi.responses.StreamingResponse:

    if request_id is not None and log_path is not None:
        raise fastapi.HTTPException(
            status_code=400,
            detail='Only one of request_id and log_path can be provided')

    if request_id is None and log_path is None:
        request_id = requests_lib.get_latest_request_id()
        if request_id is None:
            raise fastapi.HTTPException(status_code=404,
                                        detail='No request found')

    if request_id is not None:
        request_task = requests_lib.get_request(request_id)
        if request_task is None:
            print(f'No task with request ID {request_id}')
            raise fastapi.HTTPException(
                status_code=404, detail=f'Request {request_id} not found')
        log_path_to_stream = request_task.log_path
    else:
        # Make sure the log path is under ~/sky_logs.
        assert log_path is not None, (request_id, log_path)
        resolved_log_path = pathlib.Path(log_path).expanduser().resolve()
        if not str(resolved_log_path).startswith(
                os.path.expanduser(constants.SKY_LOGS_DIRECTORY)):
            raise fastapi.HTTPException(
                status_code=400, detail=f'Unauthorized log path: {log_path}')
        log_path_to_stream = resolved_log_path

    return fastapi.responses.StreamingResponse(
        log_streamer(request_id, log_path_to_stream, plain_logs),
        media_type='text/plain',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'  # Disable nginx buffering if present
        })


@app.post('/abort')
async def abort(request: fastapi.Request, abort_body: payloads.RequestIdBody):
    """Abort requests."""
    # Create a list of target abort requests.
    request_ids = []
    if abort_body.all:
        print('Aborting all requests...')
        request_ids = [
            request_task.request_id
            for request_task in requests_lib.get_request_tasks(status=[
                requests_lib.RequestStatus.RUNNING,
                requests_lib.RequestStatus.PENDING
            ])
        ]
    if abort_body.request_id is not None:
        print(f'Aborting request ID: {abort_body.request_id}')
        request_ids.append(abort_body.request_id)

    # Abort the target requests.
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='kill_request_processes',
        request_body=payloads.KillRequestProcessesBody(request_ids=request_ids),
        func=requests_lib.kill_requests,
        schedule_type=executor.ScheduleType.NON_BLOCKING,
    )


@app.get('/requests')
async def requests(
    request: fastapi.Request, request_id: Optional[str] = None,
    all: bool = False) -> List[requests_lib.RequestPayload]:
    """Get the list of requests."""
    del request  # Unused.
    if request_id is None:
        if not all:
            statuses = [
                requests_lib.RequestStatus.PENDING,
                requests_lib.RequestStatus.RUNNING,
            ]
        else:
            statuses = None
        return [
            request_task.readable_encode()
            for request_task in requests_lib.get_request_tasks(status=statuses)
        ]
    else:
        request_task = requests_lib.get_request(request_id)
        if request_task is None:
            raise fastapi.HTTPException(
                status_code=404,
                detail=f'Request {request_id} not found')
        return [request_task.readable_encode()]


@app.get('/health', response_class=fastapi.responses.PlainTextResponse)
async def health() -> str:
    return (f'SkyPilot API Server: {colorama.Style.BRIGHT}{colorama.Fore.GREEN}'
            f'Healthy{colorama.Style.RESET_ALL}\n')


@app.websocket('/kubernetes-pod-ssh-proxy')
async def kubernetes_pod_ssh_proxy(
    websocket: fastapi.WebSocket,
    cluster_name_body: payloads.ClusterNameBody = fastapi.Depends()):
    """Proxies SSH port 22 to the Kubernetes pod with websocket."""
    await websocket.accept()
    cluster_name = cluster_name_body.cluster_name
    logger.info(f'WebSocket connection accepted for cluster: {cluster_name}')

    cluster_records = core.status(cluster_name, all_users=True)
    cluster_record = cluster_records[0]
    if cluster_record['status'] != status_lib.ClusterStatus.UP:
        raise fastapi.HTTPException(
            status_code=400, detail=f'Cluster {cluster_name} is not running')

    handle = cluster_record['handle']
    assert handle is not None, 'Cluster handle is None'
    if not isinstance(handle.launched_resources.cloud, clouds.Kubernetes):
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Cluster {cluster_name} is not a Kubernetes cluster'
            'Use ssh to connect to the cluster instead.')

    config = common_utils.read_yaml(handle.cluster_yaml)
    context = kubernetes_utils.get_context_from_config(config['provider'])
    namespace = kubernetes_utils.get_namespace_from_config(config['provider'])
    pod_name = handle.cluster_name_on_cloud + '-head'

    kubectl_cmd = [
        'kubectl',
        f'--namespace={namespace}',
        f'--context={context}',
        'port-forward',
        f'pod/{pod_name}',
        ':22',
    ]
    proc = await asyncio.create_subprocess_exec(
        *kubectl_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT)
    logger.info(f'Started kubectl port-forward with command: {kubectl_cmd}')

    # Wait for port-forward to be ready and get the local port
    local_port = None
    assert proc.stdout is not None
    while True:
        stdout_line = await proc.stdout.readline()
        if stdout_line:
            decoded_line = stdout_line.decode()
            logger.info(f'kubectl port-forward stdout: {decoded_line}')
            if 'Forwarding from 127.0.0.1' in decoded_line:
                port_str = decoded_line.split(':')[-1]
                local_port = int(port_str.replace(' -> ', ':').split(':')[0])
                break
        else:
            await websocket.close()
            return

    logger.info(f'Starting port-forward to local port: {local_port}')
    try:
        # Connect to the local port
        reader, writer = await asyncio.open_connection('127.0.0.1', local_port)

        async def websocket_to_ssh():
            try:
                async for message in websocket.iter_bytes():
                    writer.write(message)
                    await writer.drain()
            except fastapi.WebSocketDisconnect:
                pass
            writer.close()

        async def ssh_to_websocket():
            try:
                while True:
                    data = await reader.read(1024)
                    if not data:
                        break
                    await websocket.send_bytes(data)
            except Exception:  # pylint: disable=broad-except
                pass
            await websocket.close()

        await asyncio.gather(websocket_to_ssh(), ssh_to_websocket())
    finally:
        proc.terminate()


if __name__ == '__main__':
    import uvicorn
    requests_lib.reset_db()

    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', default=46580, type=int)
    parser.add_argument('--reload', action='store_true')
    parser.add_argument('--deploy', action='store_true')
    cmd_args = parser.parse_args()
    num_workers = None
    if cmd_args.deploy:
        num_workers = os.cpu_count()

    workers = []
    try:
        num_queue_workers = os.cpu_count()
        if num_queue_workers is None:
            num_queue_workers = 4
        num_queue_workers *= 2
        workers = executor.start(num_queue_workers=num_queue_workers)

        logger.info('Starting API server')
        uvicorn.run('sky.api.rest:app',
                    host=cmd_args.host,
                    port=cmd_args.port,
                    reload=cmd_args.reload,
                    workers=num_workers)
    finally:
        for worker in workers:
            worker.terminate()
