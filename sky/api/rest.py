"""REST API for SkyPilot."""
import asyncio
import multiprocessing
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional
import uuid

import colorama
import fastapi
import pydantic
import starlette.middleware.base

import sky
from sky import execution
from sky import optimizer
from sky import core
from sky.api import sdk
from sky.api import rest_utils
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
    print(f'Running task {request_id}')
    with rest_utils.update_rest_task(request_id) as request_task:
        assert request_task is not None, request_id
        request_task.pid = multiprocessing.current_process().pid
        request_task.status = rest_utils.RequestStatus.RUNNING
    try:
        return_value = func(*args, **kwargs)
    except Exception as e:  # pylint: disable=broad-except
        with rest_utils.update_rest_task(request_id) as request_task:
            assert request_task is not None, request_id
            request_task.status = rest_utils.RequestStatus.FAILED
            request_task.error = e
        print(f'Task {request_id} failed')
        raise
    else:
        with rest_utils.update_rest_task(request_id) as request_task:
            assert request_task is not None, request_id
            request_task.status = rest_utils.RequestStatus.SUCCEEDED
            request_task.return_value = return_value
        print(f'Task {request_id} finished')
    return return_value


def _start_background_request(request_id: str, request_name: str,
                              func: Callable[P, Any], *args: P.args,
                              **kwargs: P.kwargs):
    """Start a task."""
    rest_task = rest_utils.RequestTask(request_id=request_id,
                                       name=request_name,
                                       entrypoint=func.__module__,
                                       args=args,
                                       kwargs=kwargs,
                                       status=rest_utils.RequestStatus.PENDING)
    rest_utils.dump_reqest(rest_task)
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
    _is_launched_by_spot_controller: bool = False


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
        _is_launched_by_spot_controller=launch_body.  # pylint: disable=protected-access
        _is_launched_by_spot_controller,
    )


class StatusBody(pydantic.BaseModel):
    cluster_names: Optional[List[str]] = None
    refresh: bool = False


class StatusReturn(pydantic.BaseModel):
    name: str
    launched_at: Optional[int] = None
    last_use: Optional[str] = None
    status: Optional[sky.ClusterStatus] = None
    handle: Optional[dict] = None
    autostop: int
    to_down: bool
    metadata: Dict[str, Any]


@app.get('/status')
async def status(status_body: StatusBody = StatusBody()) -> List[StatusReturn]:
    clusters = sdk.status(
        cluster_names=status_body.cluster_names,
        refresh=status_body.refresh,
    )
    status_returns = []
    for cluster in clusters:
        status_returns.append(
            StatusReturn(
                name=cluster['name'],
                launched_at=cluster['launched_at'],
                last_use=cluster['last_use'],
                handle=cluster['handle'].to_config(),
                status=cluster['status'],
                autostop=cluster['autostop'],
                to_down=cluster['to_down'],
                metadata=cluster['metadata'],
            ))
    return status_returns


class DownBody(pydantic.BaseModel):
    cluster_name: str
    purge: bool = False


@app.post('/down')
async def down(down_body: DownBody, request: fastapi.Request):
    _start_background_request(
        request_id=request.state.request_id,
        request_name='down',
        func=core.down,
        cluster_name=down_body.cluster_name,
        purge=down_body.purge,
    )


class RequestIdBody(pydantic.BaseModel):
    request_id: str


@app.get('/wait')
async def wait(wait_body: RequestIdBody):
    while True:
        rest_task = rest_utils.get_request(wait_body.request_id)
        if rest_task is None:
            print(f'No task with request ID {wait_body.request_id}')
            raise fastapi.HTTPException(
                status_code=404,
                detail=f'Request {wait_body.request_id} not found')
        if rest_task.status > rest_utils.RequestStatus.RUNNING:
            return rest_task.return_value
        await asyncio.sleep(1)

        # TODO(zhwu): stream the logs and handle errors.


@app.post('/abort')
async def abort(abort_body: RequestIdBody):
    print(f'Trying to kill request ID {abort_body.request_id}')
    with rest_utils.update_rest_task(abort_body.request_id) as rest_task:
        if rest_task is None:
            print(f'No task with request ID {abort_body.request_id}')
            raise fastapi.HTTPException(
                status_code=404,
                detail=f'Request {abort_body.request_id} not found')
        rest_task.status = rest_utils.RequestStatus.ABORTED
        if rest_task.pid is not None:
            subprocess_utils.kill_children_processes(parent_pid=rest_task.pid)
    print(f'Killed request: {abort_body.request_id}')


@app.get('/requests')
async def requests(request_id: Optional[str]) -> List[rest_utils.RequestTask]:
    if request_id is None:
        return rest_utils.get_request_tasks()
    else:
        request_task = rest_utils.get_request(request_id)
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
    rest_utils.reset_db()
    uvicorn.run(app, host='0.0.0.0', port=8000)
