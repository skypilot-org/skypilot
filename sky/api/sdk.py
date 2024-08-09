"""Python SDK for SkyPilot.

All the functions will return a future that can be awaited on with the `get`
method. For example:

.. code-block:: python

    request_id = sky.status()
    statuses = sky.get(request_id)

"""
import json
import os
import subprocess
import tempfile
import time
import typing
from typing import Any, List, Optional, Tuple, Union

import click
import colorama
import psutil
import requests

from sky import backends
from sky import sky_logging
from sky.api import common as api_common
from sky.api.requests import payloads
from sky.api.requests import tasks
from sky.backends import backend_utils
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import common
from sky.utils import dag_utils
from sky.utils import env_options
from sky.utils import rich_utils
from sky.utils import status_lib
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import sky

logger = sky_logging.init_logger(__name__)


@usage_lib.entrypoint
@api_common.check_health
def check(clouds: Optional[Tuple[str]], verbose: bool) -> str:
    body = payloads.CheckBody(clouds=clouds, verbose=verbose)
    response = requests.get(f'{api_common.get_server_url()}/check',
                            json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def enabled_clouds() -> str:
    response = requests.get(f'{api_common.get_server_url()}/enabled_clouds')
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def realtime_gpu_availability(name_filter: Optional[str] = None,
                              quantity_filter: Optional[int] = None) -> str:
    body = payloads.RealtimeGpuAvailabilityRequestBody(
        name_filter=name_filter,
        quantity_filter=quantity_filter,
    )
    response = requests.get(
        f'{api_common.get_server_url()}/realtime_gpu_availability',
        json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def list_accelerators(gpus_only: bool = True,
                      name_filter: Optional[str] = None,
                      region_filter: Optional[str] = None,
                      quantity_filter: Optional[int] = None,
                      clouds: Optional[Union[List[str], str]] = None,
                      all_regions: bool = False,
                      require_price: bool = True,
                      case_sensitive: bool = True) -> str:
    body = payloads.ListAcceleratorsBody(
        gpus_only=gpus_only,
        name_filter=name_filter,
        region_filter=region_filter,
        quantity_filter=quantity_filter,
        clouds=clouds,
        all_regions=all_regions,
        require_price=require_price,
        case_sensitive=case_sensitive,
    )
    response = requests.get(f'{api_common.get_server_url()}/list_accelerators',
                            json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def list_accelerator_counts(
        gpus_only: bool = True,
        name_filter: Optional[str] = None,
        region_filter: Optional[str] = None,
        quantity_filter: Optional[int] = None,
        clouds: Optional[Union[List[str], str]] = None) -> str:
    body = payloads.ListAcceleratorsBody(
        gpus_only=gpus_only,
        name_filter=name_filter,
        region_filter=region_filter,
        quantity_filter=quantity_filter,
        clouds=clouds,
    )
    response = requests.get(
        f'{api_common.get_server_url()}/list_accelerator_counts',
        json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def optimize(dag: 'sky.Dag') -> str:
    with tempfile.NamedTemporaryFile(mode='r') as f:
        dag_utils.dump_chain_dag_to_yaml(dag, f.name)
        dag_str = f.read()

    body = payloads.OptimizeBody(dag=dag_str)
    response = requests.get(f'{api_common.get_server_url()}/optimize',
                            json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def launch(
    task: Union['sky.Task', 'sky.Dag'],
    cluster_name: Optional[str] = None,
    retry_until_up: bool = False,
    idle_minutes_to_autostop: Optional[int] = None,
    dryrun: bool = False,
    down: bool = False,  # pylint: disable=redefined-outer-name
    backend: Optional[backends.Backend] = None,
    optimize_target: common.OptimizeTarget = common.OptimizeTarget.COST,
    detach_setup: bool = False,
    detach_run: bool = False,
    no_setup: bool = False,
    clone_disk_from: Optional[str] = None,
    need_confirmation: bool = False,
    # Internal only:
    # pylint: disable=invalid-name
    _is_launched_by_jobs_controller: bool = False,
    _is_launched_by_sky_serve_controller: bool = False,
    _disable_controller_check: bool = False,
) -> str:

    if cluster_name is None:
        cluster_name = backend_utils.generate_cluster_name()

    # TODO(zhwu): implement clone_disk_from
    # clone_source_str = ''
    # if clone_disk_from is not None:
    #     clone_source_str = f' from the disk of {clone_disk_from!r}'
    #     task, _ = backend_utils.check_can_clone_disk_and_override_task(
    #         clone_disk_from, cluster_name, task)

    dag = api_common.upload_mounts_to_api_server(task)

    cluster_status = None
    request_id = status([cluster_name])
    clusters = get(request_id)
    if not clusters:
        # Show the optimize log before the prompt if the cluster does not exist.
        request_id = optimize(dag)
        stream_and_get(request_id)
    else:
        cluster_record = clusters[0]
        cluster_status = cluster_record['status']

    confirm_shown = False
    if need_confirmation:
        # Prompt if (1) --cluster is None, or (2) cluster doesn't exist, or (3)
        # it exists but is STOPPED.
        prompt = None
        if cluster_status is None:
            prompt = (
                f'Launching a new cluster{cluster_name!r}. '
                # '{clone_source_str}. '
                'Proceed?')
        elif cluster_status == status_lib.ClusterStatus.STOPPED:
            prompt = (f'Restarting the stopped cluster {cluster_name!r}. '
                      'Proceed?')
        if prompt is not None:
            confirm_shown = True
            click.confirm(prompt, default=True, abort=True, show_default=True)

    if not confirm_shown:
        click.secho(f'Running task on cluster {cluster_name}...', fg='yellow')

    with tempfile.NamedTemporaryFile(mode='r') as f:
        dag_utils.dump_chain_dag_to_yaml(dag, f.name)
        dag_str = f.read()

    body = payloads.LaunchBody(
        task=dag_str,
        cluster_name=cluster_name,
        retry_until_up=retry_until_up,
        idle_minutes_to_autostop=idle_minutes_to_autostop,
        dryrun=dryrun,
        down=down,
        backend=backend.NAME if backend else None,
        optimize_target=optimize_target,
        detach_setup=detach_setup,
        detach_run=detach_run,
        no_setup=no_setup,
        clone_disk_from=clone_disk_from,
        # For internal use
        quiet_optimizer=need_confirmation,
        is_launched_by_jobs_controller=_is_launched_by_jobs_controller,
        is_launched_by_sky_serve_controller=(
            _is_launched_by_sky_serve_controller),
        disable_controller_check=_disable_controller_check,
    )
    response = requests.post(
        f'{api_common.get_server_url()}/launch',
        json=json.loads(body.model_dump_json()),
        timeout=5,
    )
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def exec(  # pylint: disable=redefined-builtin
    task: Union['sky.Task', 'sky.Dag'],
    cluster_name: Optional[str] = None,
    dryrun: bool = False,
    down: bool = False,  # pylint: disable=redefined-outer-name
    backend: Optional[backends.Backend] = None,
    detach_run: bool = False,
) -> str:
    """Execute a task."""
    dag = dag_utils.convert_entrypoint_to_dag(task)
    with tempfile.NamedTemporaryFile(mode='r') as f:
        dag_utils.dump_chain_dag_to_yaml(dag, f.name)
        dag_str = f.read()
    body = payloads.ExecBody(
        task=dag_str,
        cluster_name=cluster_name,
        dryrun=dryrun,
        down=down,
        backend=backend.NAME if backend else None,
        detach_run=detach_run,
    )

    response = requests.post(
        f'{api_common.get_server_url()}/exec',
        json=json.loads(body.model_dump_json()),
        timeout=5,
    )
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def tail_logs(cluster_name: str, job_id: Optional[int], follow: bool) -> str:
    body = payloads.ClusterJobBody(
        cluster_name=cluster_name,
        job_id=job_id,
        follow=follow,
    )
    response = requests.get(f'{api_common.get_server_url()}/logs',
                            json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def download_logs(cluster_name: str, job_ids: Optional[int]) -> str:
    body = payloads.ClusterJobsBody(
        cluster_name=cluster_name,
        job_ids=job_ids,
    )
    response = requests.get(f'{api_common.get_server_url()}/download_logs',
                            json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def start(
    cluster_name: str,
    idle_minutes_to_autostop: Optional[int] = None,
    retry_until_up: bool = False,
    down: bool = False,  # pylint: disable=redefined-outer-name
    force: bool = False,
) -> str:
    """Start a stopped cluster."""
    body = payloads.StartBody(
        cluster_name=cluster_name,
        idle_minutes_to_autostop=idle_minutes_to_autostop,
        retry_until_up=retry_until_up,
        down=down,
        force=force,
    )
    response = requests.post(
        f'{api_common.get_server_url()}/start',
        json=json.loads(body.model_dump_json()),
        timeout=5,
    )
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def down(cluster_name: str, purge: bool = False) -> str:
    """Tear down a cluster.

    Tearing down a cluster will delete all associated resources (all billing
    stops), and any data on the attached disks will be lost.  Accelerators
    (e.g., TPUs) that are part of the cluster will be deleted too.

    For local on-prem clusters, this function does not terminate the local
    cluster, but instead removes the cluster from the status table and
    terminates the calling user's running jobs.

    Args:
        cluster_names: names of clusters to down.
        purge: whether to ignore cloud provider errors (if any).

    Returns:
        A dictionary mapping cluster names to request IDs.
    """
    body = payloads.StopOrDownBody(
        cluster_name=cluster_name,
        purge=purge,
    )
    response = requests.post(
        f'{api_common.get_server_url()}/down',
        json=json.loads(body.model_dump_json()),
        timeout=5,
    )
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def stop(cluster_name: str, purge: bool = False) -> str:
    """Stop a cluster.

    Data on attached disks is not lost when a cluster is stopped.  Billing for
    the instances will stop, while the disks will still be charged.  Those
    disks will be reattached when restarting the cluster.

    Currently, spot instance clusters cannot be stopped (except for GCP, which
    does allow disk contents to be preserved when stopping spot VMs).

    Args:
        cluster_name: name of the cluster to stop.
        purge: (Advanced) Forcefully mark the cluster as stopped in SkyPilot's
            cluster table, even if the actual cluster stop operation failed on
            the cloud. WARNING: This flag should only be set sparingly in
            certain manual troubleshooting scenarios; with it set, it is the
            user's responsibility to ensure there are no leaked instances and
            related resources.
    """
    body = payloads.StopOrDownBody(
        cluster_name=cluster_name,
        purge=purge,
    )
    response = requests.post(
        f'{api_common.get_server_url()}/stop',
        json=json.loads(body.model_dump_json()),
        timeout=5,
    )
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def autostop(cluster_name: str, idle_minutes: int, down: bool = False) -> str:  # pylint: disable=redefined-outer-name
    body = payloads.AutostopBody(
        cluster_name=cluster_name,
        idle_minutes=idle_minutes,
        down=down,
    )
    response = requests.post(
        f'{api_common.get_server_url()}/autostop',
        json=json.loads(body.model_dump_json()),
        timeout=5,
    )
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def queue(cluster_name: List[str],
          skip_finished: bool = False,
          all_users: bool = False) -> str:
    body = payloads.QueueBody(
        cluster_name=cluster_name,
        skip_finished=skip_finished,
        all_users=all_users,
    )
    response = requests.get(f'{api_common.get_server_url()}/queue',
                            json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def job_status(cluster_name: str, job_ids: Optional[List[int]] = None) -> str:
    body = payloads.JobStatusBody(
        cluster_name=cluster_name,
        job_ids=job_ids,
    )
    response = requests.get(f'{api_common.get_server_url()}/job_status',
                            json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def cancel(
    cluster_name: str,
    all: bool = False,  # pylint: disable=redefined-builtin
    job_ids: Optional[List[int]] = None,
    # pylint: disable=invalid-name
    _try_cancel_if_cluster_is_init: bool = False
) -> str:
    body = payloads.CancelBody(
        cluster_name=cluster_name,
        all=all,
        job_ids=job_ids,
        try_cancel_if_cluster_is_init=_try_cancel_if_cluster_is_init,
    )
    response = requests.post(f'{api_common.get_server_url()}/cancel',
                             json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def status(
        cluster_names: Optional[List[str]] = None,
        refresh: common.StatusRefreshMode = common.StatusRefreshMode.NONE
) -> str:
    """Get the status of clusters.

    Args:
        cluster_names: names of clusters to get status for. If None, get status
            for all clusters. The cluster names specified can be in glob pattern
            (e.g., 'my-cluster-*').
        refresh: whether to refresh the status of the clusters.
    """
    # TODO(zhwu): this does not stream the logs output by logger back to the
    # user, due to the rich progress implementation.
    body = payloads.StatusBody(
        cluster_names=cluster_names,
        refresh=refresh,
    )
    response = requests.get(f'{api_common.get_server_url()}/status',
                            json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def endpoints(cluster_name: str, port: Optional[Union[int, str]] = None) -> str:
    body = payloads.EndpointBody(
        cluster_name=cluster_name,
        port=port,
    )
    response = requests.get(f'{api_common.get_server_url()}/endpoints',
                            json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def cost_report() -> str:  # pylint: disable=redefined-builtin
    response = requests.get(f'{api_common.get_server_url()}/cost_report')
    return api_common.get_request_id(response)


# === Storage APIs ===
@usage_lib.entrypoint
@api_common.check_health
def storage_ls() -> str:
    response = requests.get(f'{api_common.get_server_url()}/storage/ls')
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def storage_delete(name: str) -> str:
    body = payloads.StorageBody(name=name)
    response = requests.post(f'{api_common.get_server_url()}/storage/delete',
                             json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


# === API request API ===


@usage_lib.entrypoint
@api_common.check_health
def get(request_id: str) -> Any:
    body = payloads.RequestIdBody(request_id=request_id)
    response = requests.get(f'{api_common.get_server_url()}/get',
                            json=json.loads(body.model_dump_json()),
                            timeout=(5, None))
    request_task = tasks.RequestTask.decode(
        tasks.RequestTaskPayload(**response.json()))
    error = request_task.get_error()
    if error is not None:
        error_obj = error['object']
        if env_options.Options.SHOW_DEBUG_INFO.get():
            logger.error('=== Traceback on SkyPilot API Server ===\n'
                         f'{error_obj.stacktrace}')
        with ux_utils.print_exception_no_traceback():
            raise error_obj
    return request_task.get_return_value()


@usage_lib.entrypoint
@api_common.check_health
def stream_and_get(request_id: str) -> Any:
    """Stream the logs of a request and get the final result.

    This will block until the request is finished. The request id can be a
    prefix of the full request id.
    """
    body = payloads.RequestIdBody(request_id=request_id)
    response = requests.get(
        f'{api_common.get_server_url()}/stream',
        json=json.loads(body.model_dump_json()),
        # 5 seconds to connect, no read timeout
        timeout=(5, None),
        stream=True)

    if response.status_code != 200:
        return get(request_id)
    for line in response.iter_lines():
        if line:
            msg = line.decode('utf-8')
            msg = rich_utils.decode_rich_status(msg)
            if msg is not None:
                print(msg)
    return get(request_id)


# === API server management ===
@usage_lib.entrypoint
@api_common.check_health
def api_start():
    """Start the API server."""
    logger.info(f'SkyPilot API server: {api_common.get_server_url()}')
    if api_common.is_api_server_local():
        logger.info(
            f'Check API server logs: tail -f {constants.API_SERVER_LOGS}')
        return


@usage_lib.entrypoint
def api_stop():
    """Kill the API server."""
    # Kill the uvicorn process by name: uvicorn sky.api.rest:app
    server_url = api_common.get_server_url()
    if not api_common.is_api_server_local():
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                f'Cannot kill the API server at {server_url} because it is not '
                f'the default SkyPilot API server started locally.')

    found = False
    for process in psutil.process_iter(attrs=['pid', 'cmdline']):
        cmdline = process.info['cmdline']
        if cmdline and api_common.API_SERVER_CMD in ' '.join(cmdline):
            process.terminate()
            cnt = 0
            while cnt < 5:
                if not process.is_running():
                    break
                cnt += 1
                time.sleep(1)
            else:
                process.kill()
            found = True

    # Remove the database for requests including any files starting with
    # common.API_SERVER_REQUEST_DB_PATH
    db_path = os.path.expanduser(api_common.API_SERVER_REQUEST_DB_PATH)
    for extension in ['', '-shm', '-wal']:
        try:
            os.remove(f'{db_path}{extension}')
        except FileNotFoundError:
            logger.debug(f'Database file {db_path}{extension} not found.')

    if found:
        logger.info(f'{colorama.Fore.GREEN}SkyPilot API server stopped.'
                    f'{colorama.Style.RESET_ALL}')
    else:
        logger.info('SkyPilot API server is not running.')


# Use the same args as `docker logs`
@usage_lib.entrypoint
def api_server_logs(follow: bool = True, tail: str = 'all'):
    """Stream the API server logs."""
    server_url = api_common.get_server_url()
    if server_url != api_common.DEFAULT_SERVER_URL:
        raise RuntimeError(
            f'Cannot kill the API server at {server_url} because it is not '
            f'the default SkyPilot API server started locally.')

    tail_args = ['-f'] if follow else []
    if tail == 'all':
        tail_args.extend(['-n', '+1'])
    else:
        try:
            tail_args.extend(['-n', f'{int(tail)}'])
        except ValueError as e:
            raise ValueError(f'Invalid tail argument: {tail}') from e
    log_path = os.path.expanduser(constants.API_SERVER_LOGS)
    subprocess.run(['tail', *tail_args, f'{log_path}'], check=False)


@usage_lib.entrypoint
def abort(request_id: str) -> str:
    body = payloads.RequestIdBody(request_id=request_id)
    print(f'Sending abort request to API server for {request_id}')
    response = requests.post(f'{api_common.get_server_url()}/abort',
                             json=json.loads(body.model_dump_json()),
                             timeout=5)
    return api_common.get_request_id(response)
