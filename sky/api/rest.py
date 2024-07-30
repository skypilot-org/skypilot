"""REST API for SkyPilot."""
import argparse
import asyncio
import json
import multiprocessing
import os
import pathlib
import sys
import tempfile
import time
import traceback
from typing import Any, Callable, Dict, List, Optional
import uuid
import zipfile

import colorama
import fastapi
import pydantic
import starlette.middleware.base

from sky import core
from sky import execution
from sky import optimizer
from sky import sky_logging
from sky.api.requests import tasks
from sky.data import data_utils
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import dag_utils
from sky.utils import registry
from sky.utils import subprocess_utils
from sky.utils import ux_utils

# pylint: disable=ungrouped-imports
if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec

P = ParamSpec('P')

logger = sky_logging.init_logger(__name__)

CLIENT_DIR = pathlib.Path('~/.sky/clients')


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


def refresh_cluster_status_event():
    """Periodically refresh the cluster status."""
    while True:
        print('Refreshing cluster status...')
        core.status(refresh=True)
        print('Refreshed cluster status...')
        time.sleep(20)


# Register the events to run in the background.
events = {
    'status': refresh_cluster_status_event
}


class RequestBody(pydantic.BaseModel):
    env_vars: Dict[str, str] = {}


def wrapper(func: Callable[P, Any], request_id: str, env_vars: Dict[str, str],
            ignore_return_value: bool, *args: P.args, **kwargs: P.kwargs):
    """Wrapper for a request task."""

    def redirect_output(file):
        """Redirect stdout and stderr to the log file."""
        fd = file.fileno()  # Get the file descriptor from the file object
        # Store copies of the original stdout and stderr file descriptors
        original_stdout = os.dup(sys.stdout.fileno())
        original_stderr = os.dup(sys.stderr.fileno())

        # Copy this fd to stdout and stderr
        os.dup2(fd, sys.stdout.fileno())
        os.dup2(fd, sys.stderr.fileno())
        return original_stdout, original_stderr

    def restore_output(original_stdout, original_stderr):
        """Restore stdout and stderr to their original file descriptors. """
        os.dup2(original_stdout, sys.stdout.fileno())
        os.dup2(original_stderr, sys.stderr.fileno())

        # Close the duplicate file descriptors
        os.close(original_stdout)
        os.close(original_stderr)

    logger.info(f'Running task {request_id}')
    with tasks.update_rest_task(request_id) as request_task:
        assert request_task is not None, request_id
        log_path = request_task.log_path
        request_task.pid = multiprocessing.current_process().pid
        request_task.status = tasks.RequestStatus.RUNNING
    with log_path.open('w') as f:
        # Store copies of the original stdout and stderr file descriptors
        original_stdout, original_stderr = redirect_output(f)
        try:
            os.environ.update(env_vars)
            # Make sure the logger takes the new environment variables. This is
            # necessary because the logger is initialized before the environment
            # variables are set, such as SKYPILOT_DEBUG.
            sky_logging.reload_logger()
            return_value = func(*args, **kwargs)
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.enable_traceback():
                stacktrace = traceback.format_exc()
            e.stacktrace = stacktrace
            usage_lib.store_exception(e)
            with tasks.update_rest_task(request_id) as request_task:
                assert request_task is not None, request_id
                request_task.status = tasks.RequestStatus.FAILED
                request_task.set_error(e)
            restore_output(original_stdout, original_stderr)
            logger.info(f'Task {request_id} failed')
            return None
        else:
            with tasks.update_rest_task(request_id) as request_task:
                assert request_task is not None, request_id
                request_task.status = tasks.RequestStatus.SUCCEEDED
                if not ignore_return_value:
                    request_task.set_return_value(return_value)
            restore_output(original_stdout, original_stderr)
            logger.info(f'Task {request_id} finished')
        return return_value


def _start_background_request(request_id: str,
                              request_name: str,
                              request_body: Dict[str, Any],
                              func: Callable[P, Any],
                              ignore_return_value: bool = False,
                              *args: P.args,
                              **kwargs: P.kwargs):
    """Start a task."""
    request_task = tasks.RequestTask(request_id=request_id,
                                     name=request_name,
                                     entrypoint=func.__module__,
                                     request_body=request_body,
                                     status=tasks.RequestStatus.PENDING)
    tasks.dump_reqest(request_task)
    request_task.log_path.touch()
    kwargs['env_vars'] = request_body.get('env_vars', {})
    kwargs['ignore_return_value'] = ignore_return_value
    process = multiprocessing.Process(target=wrapper,
                                      args=(func, request_id, *args),
                                      kwargs=kwargs)
    process.start()


@app.on_event('startup')
async def startup():
    for event_id, (event_name, event) in enumerate(events.items()):
        _start_background_request(
            request_id=str(event_id),
            request_name=event_name,
            request_body={},
            func=event)


class OptimizeBody(pydantic.BaseModel):
    dag: str
    minimize: optimizer.OptimizeTarget = optimizer.OptimizeTarget.COST


@app.get('/optimize')
async def optimize(optimize_body: OptimizeBody, request: fastapi.Request):
    with tempfile.NamedTemporaryFile(mode='w') as f:
        f.write(optimize_body.dag)
        f.flush()
        dag = dag_utils.load_chain_dag_from_yaml(f.name)
    request_id = request.state.request_id
    _start_background_request(
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
    client_file_mounts_dir = (CLIENT_DIR.expanduser().resolve() / user_hash /
                              'file_mounts')
    os.makedirs(client_file_mounts_dir, exist_ok=True)
    timestamp = str(int(time.time()))
    try:
        # Save the uploaded zip file temporarily
        zip_file_path = client_file_mounts_dir / f'{timestamp}.zip'
        with open(zip_file_path, "wb") as f:
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
                    new_path = new_path
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    new_path.write_bytes(member_file.read())

        # Cleanup the temporary file
        zip_file_path.unlink()

        return {"status": "files uploaded and extracted"}
    except Exception as e:
        return {"detail": str(e)}


class LaunchBody(RequestBody):
    """The request body for the launch endpoint."""
    task: str
    cluster_name: Optional[str] = None
    retry_until_up: bool = False
    idle_minutes_to_autostop: Optional[int] = None
    dryrun: bool = False
    down: bool = False
    backend: Optional[str] = None
    optimize_target: optimizer.OptimizeTarget = optimizer.OptimizeTarget.COST
    detach_setup: bool = False
    detach_run: bool = False
    no_setup: bool = False
    clone_disk_from: Optional[str] = None
    # Internal only:
    # pylint: disable=invalid-name
    quiet_optimizer: bool = False
    is_launched_by_jobs_controller: bool = False
    is_launched_by_sky_serve_controller: bool = False
    disable_controller_check: bool = False


@app.post('/launch')
async def launch(launch_body: LaunchBody, request: fastapi.Request):
    """Launch a task.

    Args:
        task: The YAML string of the task to launch.
    """
    user_hash = launch_body.env_vars.get(constants.USER_ID_ENV_VAR, 'unknown')

    timestamp = str(int(time.time()))
    client_dir = (CLIENT_DIR.expanduser().resolve() / user_hash)
    client_task_dir = client_dir / 'tasks'
    client_task_dir.mkdir(parents=True, exist_ok=True)
    cluster_name = launch_body.cluster_name

    client_task_path = client_task_dir / f'{cluster_name}-{timestamp}.yaml'
    client_task_path.write_text(launch_body.task)

    client_file_mounts_dir = client_dir / 'file_mounts'

    task_configs = common_utils.read_yaml_all(str(client_task_path))

    for task_config in task_configs:
        if task_config is None:
            continue
        file_mounts_mapping = task_config.get('file_mounts_mapping', {})
        if not file_mounts_mapping:
            continue
        if 'workdir' in task_config:
            workdir = task_config['workdir']
            task_config['workdir'] = str(
                client_file_mounts_dir /
                file_mounts_mapping[workdir].lstrip('/'))
        if 'file_mounts' in task_config:
            file_mounts = task_config['file_mounts']
            for dst, src in file_mounts.items():
                if isinstance(src, str):
                    if not data_utils.is_cloud_store_url(src):
                        file_mounts[dst] = str(
                            client_file_mounts_dir /
                            file_mounts_mapping[src].lstrip('/'))
                elif isinstance(src, dict):
                    if 'source' in src:
                        source = src['source']
                        if not data_utils.is_cloud_store_url(source):
                            src['source'] = str(
                                client_file_mounts_dir /
                                file_mounts_mapping[source].lstrip('/'))
                else:
                    raise ValueError(f'Unexpected file_mounts value: {src}')

    print(task_configs)
    translated_client_task_path = client_dir / f'{timestamp}_translated.yaml'
    common_utils.dump_yaml(translated_client_task_path, task_configs)

    dag = dag_utils.load_chain_dag_from_yaml(str(translated_client_task_path))

    backend = registry.BACKEND_REGISTRY.from_str(launch_body.backend)
    request_id = request.state.request_id
    _start_background_request(
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


class StopBody(pydantic.BaseModel):
    cluster_name: str
    purge: bool = False


@app.post('/stop')
async def stop(request: fastapi.Request, stop_body: StopBody):
    _start_background_request(
        request_id=request.state.request_id,
        request_name='stop',
        request_body=json.loads(stop_body.model_dump_json()),
        func=core.stop,
        cluster_name=stop_body.cluster_name,
        purge=stop_body.purge,
    )



class StatusBody(pydantic.BaseModel):
    cluster_names: Optional[List[str]] = None
    refresh: bool = False


# class StatusReturn(pydantic.BaseModel):
#     name: str
#     launched_at: Optional[int] = None
#     last_use: Optional[str] = None
#     status: Optional[sky.ClusterStatus] = None
#     handle: Optional[dict] = None
#     autostop: int
#     to_down: bool
#     metadata: Dict[str, Any]


@app.get('/status')
async def status(
    request: fastapi.Request, status_body: StatusBody = StatusBody()) -> None:
    _start_background_request(
        request_id=request.state.request_id,
        request_name='status',
        request_body=json.loads(status_body.model_dump_json()),
        func=core.status,
        cluster_names=status_body.cluster_names,
        refresh=status_body.refresh,
    )


class DownBody(pydantic.BaseModel):
    cluster_name: str
    purge: bool = False


@app.post('/down')
async def down(down_body: DownBody, request: fastapi.Request):
    _start_background_request(
        request_id=request.state.request_id,
        request_name='down',
        request_body=json.loads(down_body.model_dump_json()),
        func=core.down,
        cluster_name=down_body.cluster_name,
        purge=down_body.purge,
    )


class RequestIdBody(pydantic.BaseModel):
    request_id: str


@app.get('/get')
async def get(get_body: RequestIdBody) -> tasks.RequestTaskPayload:
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
        stream_body: RequestIdBody) -> fastapi.responses.StreamingResponse:
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
async def abort(abort_body: RequestIdBody):
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
