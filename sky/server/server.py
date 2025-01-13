"""SkyPilot API Server exposing RESTful APIs."""

import argparse
import asyncio
import collections
import contextlib
import os
import pathlib
import shutil
import sys
import time
from typing import AsyncGenerator, Deque, Dict, List, Literal, Optional
import uuid
import zipfile

import aiofiles
import fastapi
from fastapi.middleware import cors
import starlette.middleware.base

import sky
from sky import check as sky_check
from sky import clouds
from sky import core
from sky import execution
from sky import global_user_state
from sky import optimizer
from sky import sky_logging
from sky.clouds import service_catalog
from sky.data import storage_utils
from sky.jobs.server import server as jobs_rest
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.serve.server import server as serve_rest
from sky.server import common
from sky.server import constants as server_constants
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import requests as requests_lib
from sky.skylet import constants
from sky.utils import common as common_lib
from sky.utils import common_utils
from sky.utils import dag_utils
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


@contextlib.asynccontextmanager
async def lifespan(app: fastapi.FastAPI):  # pylint: disable=redefined-outer-name
    """FastAPI lifespan context manager."""
    del app  # unused
    # Startup: Run background tasks
    for event in requests_lib.INTERNAL_REQUEST_EVENTS:
        executor.schedule_request(
            request_id=event.id,
            request_name=event.name,
            request_body=payloads.RequestBody(),
            func=event.event_fn,
            schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
            is_skypilot_system=True,
        )
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
async def check(request: fastapi.Request,
                check_body: payloads.CheckBody) -> None:
    """Checks enabled clouds."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='check',
        request_body=check_body,
        func=sky_check.check,
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )


@app.get('/enabled_clouds')
async def enabled_clouds(request: fastapi.Request) -> None:
    """Gets enabled clouds on the server."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='enabled_clouds',
        request_body=payloads.RequestBody(),
        func=core.enabled_clouds,
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )


@app.post('/realtime_kubernetes_gpu_availability')
async def realtime_kubernetes_gpu_availability(
    request: fastapi.Request,
    realtime_gpu_availability_body: payloads.RealtimeGpuAvailabilityRequestBody
) -> None:
    """Gets real-time Kubernetes GPU availability."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='realtime_kubernetes_gpu_availability',
        request_body=realtime_gpu_availability_body,
        func=core.realtime_kubernetes_gpu_availability,
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )


@app.post('/kubernetes_node_info')
async def kubernetes_node_info(
        request: fastapi.Request,
        kubernetes_node_info_body: payloads.KubernetesNodeInfoRequestBody
) -> None:
    """Gets Kubernetes node information."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='kubernetes_node_info',
        request_body=kubernetes_node_info_body,
        func=kubernetes_utils.get_kubernetes_node_info,
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )


@app.get('/status_kubernetes')
async def status_kubernetes(request: fastapi.Request) -> None:
    """Gets Kubernetes status."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='status_kubernetes',
        request_body=payloads.RequestBody(),
        func=core.status_kubernetes,
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )


@app.post('/list_accelerators')
async def list_accelerators(
        request: fastapi.Request,
        list_accelerator_counts_body: payloads.ListAcceleratorsBody) -> None:
    """Gets list of accelerators from cloud catalog."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='list_accelerators',
        request_body=list_accelerator_counts_body,
        func=service_catalog.list_accelerators,
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )


@app.post('/list_accelerator_counts')
async def list_accelerator_counts(
        request: fastapi.Request,
        list_accelerator_counts_body: payloads.ListAcceleratorCountsBody
) -> None:
    """Gets list of accelerator counts from cloud catalog."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='list_accelerator_counts',
        request_body=list_accelerator_counts_body,
        func=service_catalog.list_accelerator_counts,
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )


@app.post('/validate')
async def validate(validate_body: payloads.ValidateBody) -> None:
    """Validates the user's DAG."""
    # TODO(SKY-1035): validate if existing cluster satisfies the requested
    # resources, e.g. sky exec --gpus V100:8 existing-cluster-with-no-gpus
    logger.debug(f'Validating tasks: {validate_body.dag}')
    dag = dag_utils.load_chain_dag_from_yaml_str(validate_body.dag)
    for task in dag.tasks:
        # Will validate workdir and file_mounts in the backend, as those need
        # to be validated after the files are uploaded to the SkyPilot API
        # server with `upload_mounts_to_api_server`.
        task.validate_name()
        task.validate_run()
        for r in task.resources:
            r.validate()


@app.post('/optimize')
async def optimize(optimize_body: payloads.OptimizeBody,
                   request: fastapi.Request) -> None:
    """Optimizes the user's DAG."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='optimize',
        request_body=optimize_body,
        ignore_return_value=True,
        func=optimizer.Optimizer.optimize,
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )


@app.post('/upload')
async def upload_zip_file(request: fastapi.Request, user_hash: str) -> None:
    """Uploads a zip file to the API server."""
    # TODO(1271): We need to double check security of uploading zip file.
    client_file_mounts_dir = (common.CLIENT_DIR.expanduser().resolve() /
                              user_hash / 'file_mounts')
    os.makedirs(client_file_mounts_dir, exist_ok=True)
    timestamp = str(int(time.time()))
    # Save the uploaded zip file temporarily
    zip_file_path = client_file_mounts_dir / f'{timestamp}.zip'
    async with aiofiles.open(zip_file_path, 'wb') as f:
        async for chunk in request.stream():
            await f.write(chunk)

    with zipfile.ZipFile(zip_file_path, 'r') as zipf:
        for member in zipf.namelist():
            # Determine the new path
            filename = os.path.basename(member)
            original_path = os.path.normpath(member)
            new_path = client_file_mounts_dir / original_path.lstrip('/')

            if not filename:  # This is for directories, skip
                new_path.mkdir(parents=True, exist_ok=True)
                continue
            new_path.parent.mkdir(parents=True, exist_ok=True)
            with zipf.open(member) as member_file, new_path.open('wb') as f:
                # Use shutil.copyfileobj to copy files in chunks, so it does
                # not load the entire file into memory.
                shutil.copyfileobj(member_file, f)

    # Cleanup the temporary file
    zip_file_path.unlink()


@app.post('/launch')
async def launch(launch_body: payloads.LaunchBody,
                 request: fastapi.Request) -> None:
    """Launches a cluster or task."""
    request_id = request.state.request_id
    executor.schedule_request(
        request_id,
        request_name='launch',
        request_body=launch_body,
        func=execution.launch,
        schedule_type=requests_lib.ScheduleType.BLOCKING,
    )


@app.post('/exec')
# pylint: disable=redefined-builtin
async def exec(request: fastapi.Request, exec_body: payloads.ExecBody) -> None:
    """Executes a task on an existing cluster."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='exec',
        request_body=exec_body,
        func=execution.exec,
        schedule_type=requests_lib.ScheduleType.BLOCKING,
    )


@app.post('/stop')
async def stop(request: fastapi.Request,
               stop_body: payloads.StopOrDownBody) -> None:
    """Stops a cluster."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='stop',
        request_body=stop_body,
        func=core.stop,
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )


@app.post('/status')
async def status(
    request: fastapi.Request,
    status_body: payloads.StatusBody = payloads.StatusBody()
) -> None:
    """Gets cluster statuses."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='status',
        request_body=status_body,
        func=core.status,
        schedule_type=(requests_lib.ScheduleType.BLOCKING if
                       status_body.refresh != common_lib.StatusRefreshMode.NONE
                       else requests_lib.ScheduleType.NON_BLOCKING),
    )


@app.post('/endpoints')
async def endpoints(request: fastapi.Request,
                    endpoint_body: payloads.EndpointsBody) -> None:
    """Gets the endpoint for a given cluster and port number (endpoint)."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='endpoints',
        request_body=endpoint_body,
        func=core.endpoints,
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )


@app.post('/down')
async def down(request: fastapi.Request,
               down_body: payloads.StopOrDownBody) -> None:
    """Tears down a cluster."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='down',
        request_body=down_body,
        func=core.down,
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )


@app.post('/start')
async def start(request: fastapi.Request,
                start_body: payloads.StartBody) -> None:
    """Restarts a cluster."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='start',
        request_body=start_body,
        func=core.start,
        schedule_type=requests_lib.ScheduleType.BLOCKING,
    )


@app.post('/autostop')
async def autostop(request: fastapi.Request,
                   autostop_body: payloads.AutostopBody) -> None:
    """Schedules an autostop/autodown for a cluster."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='autostop',
        request_body=autostop_body,
        func=core.autostop,
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )


@app.post('/queue')
async def queue(request: fastapi.Request,
                queue_body: payloads.QueueBody) -> None:
    """Gets the job queue of a cluster."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='queue',
        request_body=queue_body,
        func=core.queue,
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )


@app.post('/job_status')
async def job_status(request: fastapi.Request,
                     job_status_body: payloads.JobStatusBody) -> None:
    """Gets the status of a job."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='job_status',
        request_body=job_status_body,
        func=core.job_status,
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )


@app.post('/cancel')
async def cancel(request: fastapi.Request,
                 cancel_body: payloads.CancelBody) -> None:
    """Cancels jobs on a cluster."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='cancel',
        request_body=cancel_body,
        func=core.cancel,
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )


@app.post('/logs')
async def logs(
    request: fastapi.Request, cluster_job_body: payloads.ClusterJobBody,
    background_tasks: fastapi.BackgroundTasks
) -> fastapi.responses.StreamingResponse:
    """Tails the logs of a job."""

    async def on_disconnect():
        logger.info(f'User terminated the connection for request '
                    f'{request.state.request_id}')
        requests_lib.kill_requests([request.state.request_id])

    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='logs',
        request_body=cluster_job_body,
        func=core.tail_logs,
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )
    request_task = requests_lib.get_request(request.state.request_id)

    # The background task will be run after returning a response.
    # https://fastapi.tiangolo.com/tutorial/background-tasks/
    background_tasks.add_task(on_disconnect)
    return fastapi.responses.StreamingResponse(
        log_streamer(request_task.request_id, request_task.log_path),
        media_type='text/plain',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'  # Disable nginx buffering if present
        })


@app.post('/download_logs')
async def download_logs(request: fastapi.Request,
                        cluster_jobs_body: payloads.ClusterJobsBody) -> None:
    """Downloads the logs of a job."""
    user_hash = cluster_jobs_body.env_vars[constants.USER_ID_ENV_VAR]
    logs_dir_on_api_server = common.api_server_user_logs_dir_prefix(user_hash)
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
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )


@app.post('/download')
async def download(download_body: payloads.DownloadBody) -> None:
    """Downloads a folder from the cluster to the local machine."""
    folder_paths = [
        pathlib.Path(folder_path) for folder_path in download_body.folder_paths
    ]
    user_hash = download_body.env_vars[constants.USER_ID_ENV_VAR]
    logs_dir_on_api_server = common.api_server_user_logs_dir_prefix(user_hash)
    for folder_path in folder_paths:
        if not str(folder_path).startswith(str(logs_dir_on_api_server)):
            raise fastapi.HTTPException(
                status_code=400, detail=f'Invalid folder path: {folder_path}')

        if not folder_path.expanduser().resolve().exists():
            raise fastapi.HTTPException(
                status_code=404, detail=f'Folder not found: {folder_path}')

    # Create a temporary zip file
    log_id = str(uuid.uuid4().hex)
    zip_filename = f'folder_{log_id}.zip'
    zip_path = pathlib.Path(
        logs_dir_on_api_server).expanduser().resolve() / zip_filename

    try:
        folders = [
            str(folder_path.expanduser().resolve())
            for folder_path in folder_paths
        ]
        storage_utils.zip_files_and_folders(folders, zip_path)

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


@app.get('/cost_report')
async def cost_report(request: fastapi.Request) -> None:
    """Gets the cost report of a cluster."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='cost_report',
        request_body=payloads.RequestBody(),
        func=core.cost_report,
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )


@app.get('/storage/ls')
async def storage_ls(request: fastapi.Request) -> None:
    """Gets the storages."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='storage_ls',
        request_body=payloads.RequestBody(),
        func=core.storage_ls,
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )


@app.post('/storage/delete')
async def storage_delete(request: fastapi.Request,
                         storage_body: payloads.StorageBody) -> None:
    """Deletes a storage."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='storage_delete',
        request_body=storage_body,
        func=core.storage_delete,
        schedule_type=requests_lib.ScheduleType.BLOCKING,
    )


@app.post('/local_up')
async def local_up(request: fastapi.Request,
                   local_up_body: payloads.LocalUpBody) -> None:
    """Launches a Kubernetes cluster on API server."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='local_up',
        request_body=local_up_body,
        func=core.local_up,
        schedule_type=requests_lib.ScheduleType.BLOCKING,
    )


@app.post('/local_down')
async def local_down(request: fastapi.Request) -> None:
    """Tears down the Kubernetes cluster started by local_up."""
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='local_down',
        request_body=payloads.RequestBody(),
        func=core.local_down,
        schedule_type=requests_lib.ScheduleType.BLOCKING,
    )


# === API server related APIs ===


@app.get('/api/get')
async def api_get(request_id: str) -> requests_lib.RequestPayload:
    """Gets a request with a given request ID prefix."""
    while True:
        request_task = requests_lib.get_request(request_id)
        if request_task is None:
            print(f'No task with request ID {request_id}', flush=True)
            raise fastapi.HTTPException(
                status_code=404, detail=f'Request {request_id} not found')
        if request_task.status > requests_lib.RequestStatus.RUNNING:
            return request_task.encode()
        # Sleep 0 to yield, so other coroutines can run. This busy waiting
        # loop is performance critical for short-running requests, so we do
        # not want to yield too long.
        await asyncio.sleep(0)


async def _yield_log_file_with_payloads_skipped(
        log_file) -> AsyncGenerator[str, None]:
    async for line in log_file:
        if not line:
            return
        is_payload, line_str = message_utils.decode_payload(
            line.decode('utf-8'), raise_for_mismatch=False)
        if is_payload:
            continue

        yield line_str


async def log_streamer(request_id: Optional[str],
                       log_path: pathlib.Path,
                       plain_logs: bool = False,
                       tail: Optional[int] = None,
                       follow: bool = True) -> AsyncGenerator[str, None]:
    """Streams the logs of a request."""
    if request_id is not None:
        status_msg = rich_utils.EncodedStatusMessage(
            f'[dim]Checking request: {request_id}[/dim]')
        request_task = requests_lib.get_request(request_id)

        if request_task is None:
            raise fastapi.HTTPException(
                status_code=404, detail=f'Request {request_id} not found')
        request_id = request_task.request_id

        # Do not show the waiting spinner if the request is a fast, non-blocking
        # request.
        show_request_waiting_spinner = (not plain_logs and
                                        request_task.schedule_type
                                        == requests_lib.ScheduleType.BLOCKING)

        if show_request_waiting_spinner:
            yield status_msg.init()
            yield status_msg.start()
        is_waiting_msg_logged = False
        waiting_msg = (f'Waiting for {request_task.name!r} request to be '
                       f'scheduled: {request_id}')
        while request_task.status < requests_lib.RequestStatus.RUNNING:
            if show_request_waiting_spinner:
                yield status_msg.update(f'[dim]{waiting_msg}[/dim]')
            elif plain_logs and not is_waiting_msg_logged:
                is_waiting_msg_logged = True
                # Use smaller padding (1024 bytes) to force browser rendering
                yield f'{waiting_msg}' + ' ' * 4096 + '\n'
            # Sleep 0 to yield, so other coroutines can run. This busy waiting
            # loop is performance critical for short-running requests, so we do
            # not want to yield too long.
            await asyncio.sleep(0)
            request_task = requests_lib.get_request(request_id)
            if not follow:
                break
        if show_request_waiting_spinner:
            yield status_msg.stop()

    # Find last n lines of the log file. Do not read the whole file into memory.
    async with aiofiles.open(str(log_path), 'rb') as f:
        if tail is not None:
            # TODO(zhwu): this will include the control lines for rich status,
            # which may not lead to exact tail lines when showing on the client
            # side.
            lines: Deque[str] = collections.deque(maxlen=tail)
            async for line_str in _yield_log_file_with_payloads_skipped(f):
                lines.append(line_str)
            for line_str in lines:
                yield line_str

        while True:
            line: Optional[bytes] = await f.readline()
            if not line:
                if request_id is not None:
                    request_task = requests_lib.get_request(request_id)
                    if request_task.status > requests_lib.RequestStatus.RUNNING:
                        if (request_task.status ==
                                requests_lib.RequestStatus.CANCELLED):
                            yield (f'{request_task.name!r} request {request_id}'
                                   ' cancelled\n')
                        break
                if not follow:
                    break

                # Sleep 0 to yield, so other coroutines can run. This busy
                # waiting loop is performance critical for short-running
                # requests, so we do not want to yield too long.
                await asyncio.sleep(0)
                continue
            line_str = line.decode('utf-8')
            if plain_logs:
                is_payload, line_str = message_utils.decode_payload(
                    line_str, raise_for_mismatch=False)
                if is_payload:
                    # Sleep 0 to yield, so other coroutines can run. This busy
                    # waiting loop is performance critical for short-running
                    # requests, so we do not want to yield too long.
                    await asyncio.sleep(0)
                    continue
            yield line_str
            await asyncio.sleep(0)  # Allow other tasks to run


@app.get('/api/stream')
async def stream(
    request: fastapi.Request,
    request_id: Optional[str] = None,
    log_path: Optional[str] = None,
    tail: Optional[int] = None,
    follow: bool = True,
    # Choices: 'auto', 'plain', 'html', 'console'
    # 'auto': automatically choose between HTML and plain text
    #         based on the request source
    # 'plain': plain text for HTML clients
    # 'html': HTML for browsers
    # 'console': console for CLI/API clients
    # pylint: disable=redefined-builtin
    format: Literal['auto', 'plain', 'html', 'console'] = 'auto',
) -> fastapi.responses.Response:
    """Streams the logs of a request.

    When format is 'auto' and the request is coming from a browser, the response
    is a HTML page with JavaScript to handle streaming, which will request the
    API server again with format='plain' to get the actual log content.

    Args:
        request_id: Request ID to stream logs for.
        log_path: Log path to stream logs for.
        tail: Number of lines to stream from the end of the log file.
        follow: Whether to follow the log file.
        format: Response format - 'auto' (HTML for browsers, plain for HTML
            clients, console for CLI/API clients), 'plain' (force plain text),
            'html' (force HTML), or 'console' (force console)
    """
    if request_id is not None and log_path is not None:
        raise fastapi.HTTPException(
            status_code=400,
            detail='Only one of request_id and log_path can be provided')

    if request_id is None and log_path is None:
        request_id = requests_lib.get_latest_request_id()
        if request_id is None:
            raise fastapi.HTTPException(status_code=404,
                                        detail='No request found')

    # Determine if we should use HTML format
    if format == 'auto':
        # Check if request is coming from a browser
        user_agent = request.headers.get('user-agent', '').lower()
        use_html = any(browser in user_agent
                       for browser in ['mozilla', 'chrome', 'safari', 'edge'])
    else:
        use_html = format == 'html'

    if use_html:
        # Return HTML page with JavaScript to handle streaming
        stream_url = request.url.include_query_params(format='plain')
        html_dir = pathlib.Path(__file__).parent / 'html'
        with open(html_dir / 'log.html', 'r', encoding='utf-8') as file:
            html_content = file.read()
        return fastapi.responses.HTMLResponse(
            html_content.replace('{stream_url}', str(stream_url)),
            headers={
                'Cache-Control': 'no-cache, no-transform',
                'X-Accel-Buffering': 'no'
            })

    # Original plain text streaming logic
    if request_id is not None:
        request_task = requests_lib.get_request(request_id)
        if request_task is None:
            print(f'No task with request ID {request_id}')
            raise fastapi.HTTPException(
                status_code=404, detail=f'Request {request_id} not found')
        log_path_to_stream = request_task.log_path
    else:
        assert log_path is not None, (request_id, log_path)
        if log_path == constants.API_SERVER_LOGS:
            resolved_log_path = pathlib.Path(
                constants.API_SERVER_LOGS).expanduser()
        else:
            # This should be a log path under ~/sky_logs.
            resolved_logs_directory = pathlib.Path(
                constants.SKY_LOGS_DIRECTORY).expanduser().resolve()
            resolved_log_path = resolved_logs_directory.joinpath(
                log_path).resolve()
            # Make sure the log path is under ~/sky_logs. Prevents path
            # gtraversal using ..
            if os.path.commonpath([resolved_log_path, resolved_logs_directory
                                  ]) != str(resolved_logs_directory):
                raise fastapi.HTTPException(
                    status_code=400,
                    detail=f'Unauthorized log path: {log_path}')

        log_path_to_stream = resolved_log_path
    return fastapi.responses.StreamingResponse(
        content=log_streamer(request_id,
                             log_path_to_stream,
                             plain_logs=format == 'plain',
                             tail=tail,
                             follow=follow),
        media_type='text/plain',
        headers={
            'Cache-Control': 'no-cache, no-transform',
            'X-Accel-Buffering': 'no',
            'Transfer-Encoding': 'chunked'
        },
    )


@app.post('/api/cancel')
async def api_cancel(request: fastapi.Request,
                     abort_body: payloads.RequestIdBody) -> None:
    """Cancels requests."""
    # Create a list of target abort requests.
    request_ids = []
    if abort_body.all:
        print('Cancelling all requests...')
        request_ids = [
            request_task.request_id for request_task in
            requests_lib.get_request_tasks(status=[
                requests_lib.RequestStatus.RUNNING,
                requests_lib.RequestStatus.PENDING
            ],
                                           user_id=abort_body.user_id)
        ]
    if abort_body.request_id is not None:
        print(f'Cancelling request ID: {abort_body.request_id}')
        request_ids.append(abort_body.request_id)

    # Abort the target requests.
    executor.schedule_request(
        request_id=request.state.request_id,
        request_name='cancel_requests',
        request_body=payloads.KillRequestProcessesBody(request_ids=request_ids),
        func=requests_lib.kill_requests,
        schedule_type=requests_lib.ScheduleType.NON_BLOCKING,
    )


@app.get('/api/status')
async def api_status(
    request_id: Optional[str] = None,
    all: bool = False  # pylint: disable=redefined-builtin
) -> List[requests_lib.RequestPayload]:
    """Gets the list of requests."""
    if request_id is None:
        statuses = None
        if not all:
            statuses = [
                requests_lib.RequestStatus.PENDING,
                requests_lib.RequestStatus.RUNNING,
            ]
        return [
            request_task.readable_encode()
            for request_task in requests_lib.get_request_tasks(status=statuses)
        ]
    else:
        request_task = requests_lib.get_request(request_id)
        if request_task is None:
            raise fastapi.HTTPException(
                status_code=404, detail=f'Request {request_id} not found')
        return [request_task.readable_encode()]


@app.get('/api/health')
async def health() -> Dict[str, str]:
    """Checks the health of the API server.

    Returns:
        A dictionary with the following keys:
        - status: str; The status of the API server.
        - api_version: str; The API version of the API server.
        - commit: str; The commit hash of SkyPilot used for API server.
        - version: str; The version of SkyPilot used for API server.
    """
    return {
        'status': common.ApiServerStatus.HEALTHY.value,
        'api_version': server_constants.API_VERSION,
        'commit': sky.__commit__,
        'version': sky.__version__,
    }


@app.websocket('/kubernetes-pod-ssh-proxy')
async def kubernetes_pod_ssh_proxy(
    websocket: fastapi.WebSocket,
    cluster_name_body: payloads.ClusterNameBody = fastapi.Depends()
) -> None:
    """Proxies SSH to the Kubernetes pod with websocket."""
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

    kubernetes_args = []
    if namespace is not None:
        kubernetes_args.append(f'--namespace={namespace}')
    if context is not None:
        kubernetes_args.append(f'--context={context}')

    kubectl_cmd = [
        'kubectl',
        *kubernetes_args,
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


# === Internal APIs ===
@app.get('/api/completion/cluster_name')
async def complete_cluster_name(incomplete: str,) -> List[str]:
    return global_user_state.get_cluster_names_start_with(incomplete)


@app.get('/api/completion/storage_name')
async def complete_storage_name(incomplete: str,) -> List[str]:
    return global_user_state.get_storage_names_start_with(incomplete)


if __name__ == '__main__':
    import uvicorn
    requests_lib.reset_db_and_logs()

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
        workers = executor.start(cmd_args.deploy)
        logger.info('Starting SkyPilot API server')
        uvicorn.run('sky.server.server:app',
                    host=cmd_args.host,
                    port=cmd_args.port,
                    reload=cmd_args.reload,
                    workers=num_workers)
    finally:
        for worker in workers:
            worker.terminate()
