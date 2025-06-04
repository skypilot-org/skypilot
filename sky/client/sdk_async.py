"""Async client-side Python SDK for SkyPilot.

All functions will return a future that can be awaited on.

Usage example:

.. code-block:: python

    request_id = await sky.status()
    statuses = await sky.get(request_id)

"""
import logging
import typing
from typing import Any, Dict, List, Optional, Tuple, Union

import aiohttp
import colorama

from sky import admin_policy
from sky import backends
from sky import exceptions
from sky import models
from sky import sky_logging
from sky.client import common as client_common
from sky.client import sdk
import sky.clouds.service_catalog
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.server import common as server_common
from sky.server.requests import requests as requests_lib
from sky.skylet import job_lib
from sky.usage import usage_lib
from sky.utils import annotations
from sky.utils import common
from sky.utils import env_options
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import io

    import sky

logger = sky_logging.init_logger(__name__)
logging.getLogger('httpx').setLevel(logging.CRITICAL)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def get(request_id: str) -> Any:
    """Async version of get() that waits for and gets the result of a request.

    Args:
        request_id: The request ID of the request to get.

    Returns:
        The ``Request Returns`` of the specified request. See the documentation
        of the specific requests above for more details.

    Raises:
        Exception: It raises the same exceptions as the specific requests,
            see ``Request Raises`` in the documentation of the specific requests
            above.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(
                f'{server_common.get_server_url()}'
                f'/api/get?request_id={request_id}',
                timeout=aiohttp.ClientTimeout(
                    total=None,
                    connect=client_common.
                    API_SERVER_REQUEST_CONNECTION_TIMEOUT_SECONDS),
                cookies=server_common.get_api_cookie_jar()) as response:
            request_task = None
            if response.status == 200:
                request_task = requests_lib.Request.decode(
                    requests_lib.RequestPayload(**await response.json()))
            elif response.status == 500:
                try:
                    request_task = requests_lib.Request.decode(
                        requests_lib.RequestPayload(**await response.json()))
                    logger.debug(f'Got request with error: {request_task.name}')
                except Exception:  # pylint: disable=broad-except
                    request_task = None
            if request_task is None:
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(
                        f'Failed to get request {request_id}: '
                        f'{response.status} {await response.text()}')
            error = request_task.get_error()
            if error is not None:
                error_obj = error['object']
                if env_options.Options.SHOW_DEBUG_INFO.get():
                    stacktrace = getattr(error_obj, 'stacktrace',
                                         str(error_obj))
                    logger.error('=== Traceback on SkyPilot API Server ===\n'
                                 f'{stacktrace}')
                with ux_utils.print_exception_no_traceback():
                    raise error_obj
            if request_task.status == requests_lib.RequestStatus.CANCELLED:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.RequestCancelled(
                        f'{colorama.Fore.YELLOW}Current {request_task.name!r} '
                        f'request ({request_task.request_id}) is cancelled by '
                        f'another process. {colorama.Style.RESET_ALL}')
            return request_task.get_return_value()


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def check(infra_list: Optional[Tuple[str, ...]],
                verbose: bool,
                workspace: Optional[str] = None) -> Dict[str, List[str]]:
    """Async version of check() that checks the credentials to enable clouds."""
    request_id = sdk.check(infra_list, verbose, workspace)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def enabled_clouds(workspace: Optional[str] = None,
                         expand: bool = False) -> List[str]:
    """Async version of enabled_clouds() that gets the enabled clouds."""
    request_id = sdk.enabled_clouds(workspace, expand)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def list_accelerators(
    gpus_only: bool = True,
    name_filter: Optional[str] = None,
    region_filter: Optional[str] = None,
    quantity_filter: Optional[int] = None,
    clouds: Optional[Union[List[str], str]] = None,
    all_regions: bool = False,
    require_price: bool = True,
    case_sensitive: bool = True
) -> Dict[str, List[sky.clouds.service_catalog.common.InstanceTypeInfo]]:
    """Async version of list_accelerators() that lists the names of all
    accelerators offered by Sky."""
    request_id = sdk.list_accelerators(gpus_only, name_filter, region_filter,
                                       quantity_filter, clouds, all_regions,
                                       require_price, case_sensitive)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def list_accelerator_counts(
        gpus_only: bool = True,
        name_filter: Optional[str] = None,
        region_filter: Optional[str] = None,
        quantity_filter: Optional[int] = None,
        clouds: Optional[Union[List[str], str]] = None) -> Dict[str, List[int]]:
    """Async version of list_accelerator_counts() that lists all accelerators
      offered by Sky and available counts."""
    request_id = sdk.list_accelerator_counts(gpus_only, name_filter,
                                             region_filter, quantity_filter,
                                             clouds)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def optimize(
    dag: 'sky.Dag',
    minimize: common.OptimizeTarget = common.OptimizeTarget.COST,
    admin_policy_request_options: Optional[admin_policy.RequestOptions] = None
) -> sky.dag.Dag:
    """Async version of optimize() that finds the best execution plan for the
      given DAG."""
    request_id = sdk.optimize(dag, minimize, admin_policy_request_options)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def workspaces() -> Dict[str, Any]:
    """Async version of workspaces() that gets the workspaces."""
    request_id = sdk.workspaces()
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def launch(
    task: Union['sky.Task', 'sky.Dag'],
    cluster_name: Optional[str] = None,
    retry_until_up: bool = False,
    idle_minutes_to_autostop: Optional[int] = None,
    dryrun: bool = False,
    down: bool = False,  # pylint: disable=redefined-outer-name
    backend: Optional[backends.Backend] = None,
    optimize_target: common.OptimizeTarget = common.OptimizeTarget.COST,
    no_setup: bool = False,
    clone_disk_from: Optional[str] = None,
    fast: bool = False,
    # Internal only:
    # pylint: disable=invalid-name
    _need_confirmation: bool = False,
    _is_launched_by_jobs_controller: bool = False,
    _is_launched_by_sky_serve_controller: bool = False,
    _disable_controller_check: bool = False,
) -> Tuple[Optional[int], Optional[backends.ResourceHandle]]:
    """Async version of launch() that launches a cluster or task."""
    request_id = sdk.launch(task, cluster_name, retry_until_up,
                            idle_minutes_to_autostop, dryrun, down, backend,
                            optimize_target, no_setup, clone_disk_from, fast,
                            _need_confirmation, _is_launched_by_jobs_controller,
                            _is_launched_by_sky_serve_controller,
                            _disable_controller_check)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def exec(  # pylint: disable=redefined-builtin
    task: Union['sky.Task', 'sky.Dag'],
    cluster_name: Optional[str] = None,
    dryrun: bool = False,
    down: bool = False,  # pylint: disable=redefined-outer-name
    backend: Optional[backends.Backend] = None,
) -> Tuple[Optional[int], Optional[backends.ResourceHandle]]:
    """Async version of exec() that executes a task on an existing cluster."""
    request_id = sdk.exec(task, cluster_name, dryrun, down, backend)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def tail_logs(cluster_name: str,
                    job_id: Optional[int],
                    follow: bool,
                    tail: int = 0,
                    output_stream: Optional['io.TextIOBase'] = None) -> int:
    """Async version of tail_logs() that tails the logs of a job."""
    return sdk.tail_logs(cluster_name, job_id, follow, tail, output_stream)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def download_logs(cluster_name: str,
                        job_ids: Optional[List[str]]) -> Dict[str, str]:
    """Async version of download_logs() that downloads the logs of jobs."""
    return sdk.download_logs(cluster_name, job_ids)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def start(
    cluster_name: str,
    idle_minutes_to_autostop: Optional[int] = None,
    retry_until_up: bool = False,
    down: bool = False,  # pylint: disable=redefined-outer-name
    force: bool = False,
) -> backends.CloudVmRayResourceHandle:
    """Async version of start() that restarts a cluster."""
    request_id = sdk.start(cluster_name, idle_minutes_to_autostop,
                           retry_until_up, down, force)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def down(cluster_name: str, purge: bool = False) -> None:
    """Async version of down() that tears down a cluster."""
    request_id = sdk.down(cluster_name, purge)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def stop(cluster_name: str, purge: bool = False) -> None:
    """Async version of stop() that stops a cluster."""
    request_id = sdk.stop(cluster_name, purge)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def autostop(
        cluster_name: str,
        idle_minutes: int,
        down: bool = False  # pylint: disable=redefined-outer-name
) -> None:
    """Async version of autostop() that schedules an autostop/autodown for a
      cluster."""
    request_id = sdk.autostop(cluster_name, idle_minutes, down)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def queue(cluster_name: str,
                skip_finished: bool = False,
                all_users: bool = False) -> List[dict]:
    """Async version of queue() that gets the job queue of a cluster."""
    request_id = sdk.queue(cluster_name, skip_finished, all_users)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def job_status(
    cluster_name: str,
    job_ids: Optional[List[int]] = None
) -> Dict[Optional[int], Optional[job_lib.JobStatus]]:
    """Async version of job_status() that gets the status of jobs on a
      cluster."""
    request_id = sdk.job_status(cluster_name, job_ids)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def cancel(
    cluster_name: str,
    all: bool = False,  # pylint: disable=redefined-builtin
    all_users: bool = False,
    job_ids: Optional[List[int]] = None,
    # pylint: disable=invalid-name
    _try_cancel_if_cluster_is_init: bool = False
) -> None:
    """Async version of cancel() that cancels jobs on a cluster."""
    request_id = sdk.cancel(cluster_name, all, all_users, job_ids,
                            _try_cancel_if_cluster_is_init)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def status(
    cluster_names: Optional[List[str]] = None,
    refresh: common.StatusRefreshMode = common.StatusRefreshMode.NONE,
    all_users: bool = False,
) -> List[Dict[str, Any]]:
    """Async version of status() that gets cluster statuses."""
    request_id = sdk.status(cluster_names, refresh, all_users)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def endpoints(cluster: str,
                    port: Optional[Union[int, str]] = None) -> Dict[int, str]:
    """Async version of endpoints() that gets the endpoint for a given cluster
      and port number."""
    request_id = sdk.endpoints(cluster, port)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def cost_report() -> List[Dict[str, Any]]:
    """Async version of cost_report() that gets all cluster cost reports."""
    request_id = sdk.cost_report()
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def storage_ls() -> List[Dict[str, Any]]:
    """Async version of storage_ls() that gets the storages."""
    request_id = sdk.storage_ls()
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def storage_delete(name: str) -> None:
    """Async version of storage_delete() that deletes a storage."""
    request_id = sdk.storage_delete(name)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def local_up(gpus: bool,
                   ips: Optional[List[str]],
                   ssh_user: Optional[str],
                   ssh_key: Optional[str],
                   cleanup: bool,
                   context_name: Optional[str] = None,
                   password: Optional[str] = None) -> None:
    """Async version of local_up() that launches a Kubernetes cluster on
    local machines."""
    request_id = sdk.local_up(gpus, ips, ssh_user, ssh_key, cleanup,
                              context_name, password)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def local_down() -> None:
    """Async version of local_down() that tears down the Kubernetes cluster
    started by local_up."""
    request_id = sdk.local_down()
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def ssh_up(infra: Optional[str] = None) -> None:
    """Async version of ssh_up() that deploys the SSH Node Pools defined in
      ~/.sky/ssh_targets.yaml."""
    request_id = sdk.ssh_up(infra)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def ssh_down(infra: Optional[str] = None) -> None:
    """Async version of ssh_down() that tears down a Kubernetes cluster on SSH
    targets."""
    request_id = sdk.ssh_down(infra)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def realtime_kubernetes_gpu_availability(
    context: Optional[str] = None,
    name_filter: Optional[str] = None,
    quantity_filter: Optional[int] = None,
    is_ssh: Optional[bool] = None
) -> List[Tuple[str, List[models.RealtimeGpuAvailability]]]:
    """Async version of realtime_kubernetes_gpu_availability() that gets the
      real-time Kubernetes GPU availability."""
    request_id = sdk.realtime_kubernetes_gpu_availability(
        context, name_filter, quantity_filter, is_ssh)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def kubernetes_node_info(
        context: Optional[str] = None) -> models.KubernetesNodesInfo:
    """Async version of kubernetes_node_info() that gets the resource
    information for all the nodes in the cluster."""
    request_id = sdk.kubernetes_node_info(context)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def status_kubernetes(
) -> Tuple[List[kubernetes_utils.KubernetesSkyPilotClusterInfoPayload],
           List[kubernetes_utils.KubernetesSkyPilotClusterInfoPayload],
           List[Dict[str, Any]], Optional[str]]:
    """Async version of status_kubernetes() that gets all SkyPilot clusters
      and jobs in the Kubernetes cluster."""
    request_id = sdk.status_kubernetes()
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
async def api_cancel(request_ids: Optional[Union[str, List[str]]] = None,
                     all_users: bool = False,
                     silent: bool = False) -> List[str]:
    """Async version of api_cancel() that aborts a request or all requests."""
    request_id = sdk.api_cancel(request_ids, all_users, silent)
    return await get(request_id)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def api_status(request_ids: Optional[List[str]] = None,
               all_status: bool = False) -> List[requests_lib.RequestPayload]:
    """Async version of api_status() that lists all requests."""
    return sdk.api_status(request_ids, all_status)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def dashboard(starting_page: Optional[str] = None) -> None:
    """Async version of dashboard() that starts the dashboard for SkyPilot."""
    return sdk.dashboard(starting_page)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def api_info() -> Dict[str, Any]:
    """Async version of api_info() that gets the server's status, commit and
      version."""
    return sdk.api_info()


@usage_lib.entrypoint
@annotations.client_api
def api_stop() -> None:
    """Async version of api_stop() that stops the API server."""
    return sdk.api_stop()


@usage_lib.entrypoint
@annotations.client_api
def api_server_logs(follow: bool = True, tail: Optional[int] = None) -> None:
    """Async version of api_server_logs() that streams the API server logs."""
    return sdk.api_server_logs(follow, tail)


@usage_lib.entrypoint
@annotations.client_api
def api_login(endpoint: Optional[str] = None, get_token: bool = False) -> None:
    """Async version of api_login() that logs into a SkyPilot API server."""
    return sdk.api_login(endpoint, get_token)
