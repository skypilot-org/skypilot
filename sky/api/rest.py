"""REST API for SkyPilot."""
import argparse
import asyncio
import json
import multiprocessing
import pathlib
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional
import uuid

import colorama
import fastapi
import pydantic
import starlette.middleware.base

from sky import core
from sky import execution
from sky import optimizer
from sky import sky_logging
from sky.api.requests import tasks
from sky.utils import dag_utils
from sky.utils import registry
from sky.utils import subprocess_utils

# pylint: disable=ungrouped-imports
if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec

P = ParamSpec('P')


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


async def refresh_cluster_status_event():
    """Periodically refresh the cluster status."""
    background_tasks = fastapi.BackgroundTasks()
    while True:
        background_tasks.add_task(core.status, refresh=True)
        await asyncio.sleep(30)


# Register the events to run in the background.
events = [
    refresh_cluster_status_event,
]


def wrapper(func: Callable[P, Any], request_id: str, *args: P.args,
            **kwargs: P.kwargs):
    """Wrapper for a request task."""
    print(f'Running task {request_id}')
    with tasks.update_rest_task(request_id) as request_task:
        assert request_task is not None, request_id
        log_path = request_task.log_path
        request_task.pid = multiprocessing.current_process().pid
        request_task.status = tasks.RequestStatus.RUNNING
    with log_path.open('w') as f:
        # Redirect stdout and stderr to the log file.
        sys.stdout = sys.stderr = f
        # reconfigure logger since the logger is initialized before
        # with previous stdout/stderr
        sky_logging.reload_logger()
        try:
            return_value = func(*args, **kwargs)
        except Exception as e:  # pylint: disable=broad-except
            with tasks.update_rest_task(request_id) as request_task:
                assert request_task is not None, request_id
                request_task.status = tasks.RequestStatus.FAILED
                request_task.set_error(e)
            print(f'Task {request_id} failed')
            raise
        else:
            with tasks.update_rest_task(request_id) as request_task:
                assert request_task is not None, request_id
                request_task.status = tasks.RequestStatus.SUCCEEDED
                request_task.set_return_value(return_value)
            print(f'Task {request_id} finished')
        return return_value


def _start_background_request(request_id: str, request_name: str,
                              request_body: Dict[str, Any], func: Callable[P,
                                                                           Any],
                              *args: P.args, **kwargs: P.kwargs):
    """Start a task."""
    rest_task = tasks.RequestTask(request_id=request_id,
                                  name=request_name,
                                  entrypoint=func.__module__,
                                  request_body=request_body,
                                  status=tasks.RequestStatus.PENDING)
    tasks.dump_reqest(rest_task)
    process = multiprocessing.Process(target=wrapper,
                                      args=(func, request_id, *args),
                                      kwargs=kwargs)
    process.start()


@app.on_event('startup')
async def startup():
    for event in events:
        asyncio.create_task(event())


class LaunchBody(pydantic.BaseModel):
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
    no_setup: bool = False
    clone_disk_from: Optional[str] = None
    # Internal only:
    # pylint: disable=invalid-name
    _is_launched_by_jobs_controller: bool = False
    _is_launched_by_sky_serve_controller: bool = False
    _disable_controller_check: bool = False


@app.post('/launch')
async def launch(launch_body: LaunchBody, request: fastapi.Request):
    """Launch a task.

    Args:
        task: The YAML string of the task to launch.
    """
    with tempfile.NamedTemporaryFile(mode='w') as f:
        f.write(launch_body.task)
        f.flush()
        dag = dag_utils.load_chain_dag_from_yaml(f.name)

    backend = registry.BACKEND_REGISTRY.from_str(launch_body.backend)
    request_id = request.state.request_id
    _start_background_request(
        request_id,
        request_name='launch',
        request_body=json.loads(launch_body.json()),
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
        detach_run=True,
        no_setup=launch_body.no_setup,
        clone_disk_from=launch_body.clone_disk_from,
        _is_launched_by_jobs_controller=launch_body.
        _is_launched_by_jobs_controller,
        _is_launched_by_sky_serve_controller=launch_body.
        _is_launched_by_sky_serve_controller,
        _disable_controller_check=launch_body._disable_controller_check,
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
        request_body=json.loads(status_body.json()),
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
        request_body=json.loads(down_body.json()),
        func=core.down,
        cluster_name=down_body.cluster_name,
        purge=down_body.purge,
    )


class RequestIdBody(pydantic.BaseModel):
    request_id: str


@app.get('/get')
async def get(get_body: RequestIdBody) -> tasks.RequestTask:
    while True:
        request_task = tasks.get_request(get_body.request_id)
        if request_task is None:
            print(f'No task with request ID {get_body.request_id}')
            raise fastapi.HTTPException(
                status_code=404,
                detail=f'Request {get_body.request_id} not found')
        if request_task.status > tasks.RequestStatus.RUNNING:
            return request_task
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
            subprocess_utils.kill_children_processes(parent_pids=rest_task.pid)
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
