"""Client-side Python SDK for SkyPilot.

All functions will return a future that can be awaited on with the `get` method.

Usage example:

.. code-block:: python

    request_id = sky.status()
    statuses = sky.get(request_id)

"""
import json
import os
import pathlib
import subprocess
import tempfile
import typing
from typing import Any, Dict, List, Optional, Tuple, Union
import zipfile

import click
import colorama
import psutil
import requests

from sky import backends
from sky import sky_logging
from sky.api import common as api_common
from sky.api.requests import payloads
from sky.api.requests import requests as requests_lib
from sky.backends import backend_utils
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import annotations
from sky.utils import common
from sky.utils import dag_utils
from sky.utils import env_options
from sky.utils import rich_utils
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import sky

logger = sky_logging.init_logger(__name__)


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
def check(clouds: Optional[Tuple[str]], verbose: bool) -> str:
    body = payloads.CheckBody(clouds=clouds, verbose=verbose)
    response = requests.post(f'{api_common.get_server_url()}/check',
                             json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
def enabled_clouds() -> str:
    response = requests.get(f'{api_common.get_server_url()}/enabled_clouds')
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
def realtime_kubernetes_gpu_availability(
        context: Optional[str] = None,
        name_filter: Optional[str] = None,
        quantity_filter: Optional[int] = None) -> str:
    body = payloads.RealtimeGpuAvailabilityRequestBody(
        context=context,
        name_filter=name_filter,
        quantity_filter=quantity_filter,
    )
    response = requests.post(
        f'{api_common.get_server_url()}/realtime_kubernetes_gpu_availability',
        json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
def kubernetes_node_info(context: Optional[str] = None) -> str:
    body = payloads.KubernetesNodeInfoRequestBody(context=context)
    response = requests.get(
        f'{api_common.get_server_url()}/kubernetes_node_info',
        params=api_common.request_body_to_params(body))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
def status_kubernetes() -> str:
    response = requests.get(f'{api_common.get_server_url()}/status_kubernetes')
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
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
    response = requests.post(f'{api_common.get_server_url()}/list_accelerators',
                             json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
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
    response = requests.post(
        f'{api_common.get_server_url()}/list_accelerator_counts',
        json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
def optimize(
        dag: 'sky.Dag',
        minimize: common.OptimizeTarget = common.OptimizeTarget.COST) -> str:
    with tempfile.NamedTemporaryFile(mode='r') as f:
        dag_utils.dump_chain_dag_to_yaml(dag, f.name)
        dag_str = f.read()

    body = payloads.OptimizeBody(dag=dag_str, minimize=minimize)
    response = requests.post(f'{api_common.get_server_url()}/optimize',
                             json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
def launch(
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

    confirm_shown = False

    if need_confirmation:
        cluster_status = None
        request_id = status([cluster_name])
        clusters = get(request_id)
        if not clusters:
            # Show the optimize log before the prompt if the cluster does not
            # exist.
            request_id = optimize(dag)
            stream_and_get(request_id)
        else:
            cluster_record = clusters[0]
            cluster_status = cluster_record['status']

        # Prompt if (1) --cluster is None, or (2) cluster doesn't exist, or (3)
        # it exists but is STOPPED.
        prompt = None
        if cluster_status is None:
            prompt = (
                f'Launching a new cluster {cluster_name!r}. '
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
        no_setup=no_setup,
        clone_disk_from=clone_disk_from,
        fast=fast,
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
@annotations.public_api
def exec(  # pylint: disable=redefined-builtin
    task: Union['sky.Task', 'sky.Dag'],
    cluster_name: Optional[str] = None,
    dryrun: bool = False,
    down: bool = False,  # pylint: disable=redefined-outer-name
    backend: Optional[backends.Backend] = None,
) -> str:
    """Execute a task."""
    dag = api_common.upload_mounts_to_api_server(task, workdir_only=True)
    with tempfile.NamedTemporaryFile(mode='r') as f:
        dag_utils.dump_chain_dag_to_yaml(dag, f.name)
        dag_str = f.read()
    body = payloads.ExecBody(
        task=dag_str,
        cluster_name=cluster_name,
        dryrun=dryrun,
        down=down,
        backend=backend.NAME if backend else None,
    )

    response = requests.post(
        f'{api_common.get_server_url()}/exec',
        json=json.loads(body.model_dump_json()),
        timeout=5,
    )
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
def tail_logs(cluster_name: str,
              job_id: Optional[int],
              follow: bool,
              tail: int = 0) -> str:
    body = payloads.ClusterJobBody(
        cluster_name=cluster_name,
        job_id=job_id,
        follow=follow,
        tail=tail,
    )
    response = requests.post(f'{api_common.get_server_url()}/logs',
                             json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
def download_logs(cluster_name: str,
                  job_ids: Optional[List[str]]) -> Dict[str, str]:
    body = payloads.ClusterJobsBody(
        cluster_name=cluster_name,
        job_ids=job_ids,
    )
    response = requests.post(f'{api_common.get_server_url()}/download_logs',
                             json=json.loads(body.model_dump_json()))
    remote_path_dict = stream_and_get(api_common.get_request_id(response))
    remote2local_path_dict = {
        remote_path:
        remote_path.replace(str(api_common.api_server_logs_dir_prefix()),
                            constants.SKY_LOGS_DIRECTORY)
        for remote_path in remote_path_dict.values()
    }
    body = payloads.DownloadBody(folder_paths=list(remote_path_dict.values()),)
    response = requests.post(f'{api_common.get_server_url()}/download',
                             json=json.loads(body.model_dump_json()),
                             stream=True)
    if response.status_code == 200:
        remote_home_path = response.headers.get('X-Home-Path')
        assert remote_home_path is not None, response.headers
        with tempfile.NamedTemporaryFile(prefix='skypilot-logs-download-',
                                         delete=True) as temp_file:
            # Download the zip file from the API server to the local machine.
            for chunk in response.iter_content(chunk_size=8192):
                temp_file.write(chunk)
            temp_file.flush()

            # Unzip the downloaded file and save the logs to the correct local
            # directory.
            with zipfile.ZipFile(temp_file, 'r') as zipf:
                for member in zipf.namelist():
                    # Determine the new path
                    filename = os.path.basename(member)
                    original_dir = os.path.dirname('/' + member)
                    local_dir = original_dir.replace(remote_home_path, '~')
                    for remote_path, local_path in remote2local_path_dict.items(
                    ):
                        if local_dir.startswith(remote_path):
                            local_dir = local_dir.replace(
                                remote_path, local_path)
                            break
                    else:
                        raise ValueError(f'Invalid folder path: {original_dir}')
                    new_path = pathlib.Path(
                        local_dir).expanduser().resolve() / filename
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    with zipf.open(member) as member_file:
                        new_path.write_bytes(member_file.read())

        return {
            job_id: remote2local_path_dict[log_dir]
            for job_id, log_dir in remote_path_dict.items()
        }
    else:
        raise Exception(
            f'Failed to download logs: {response.status_code} {response.text}')


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
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
@annotations.public_api
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
@annotations.public_api
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
@annotations.public_api
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
@annotations.public_api
def queue(cluster_name: List[str],
          skip_finished: bool = False,
          all_users: bool = False) -> str:
    body = payloads.QueueBody(
        cluster_name=cluster_name,
        skip_finished=skip_finished,
        all_users=all_users,
    )
    response = requests.post(f'{api_common.get_server_url()}/queue',
                             json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
def job_status(cluster_name: str, job_ids: Optional[List[int]] = None) -> str:
    # TODO: merge this into the queue endpoint, i.e., let the queue endpoint
    # take job_ids to filter the returned jobs.
    body = payloads.JobStatusBody(
        cluster_name=cluster_name,
        job_ids=job_ids,
    )
    response = requests.post(f'{api_common.get_server_url()}/job_status',
                             json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
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
@annotations.public_api
def status(
    cluster_names: Optional[List[str]] = None,
    refresh: common.StatusRefreshMode = common.StatusRefreshMode.NONE,
    all_users: bool = False,
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
        all_users=all_users,
    )
    response = requests.post(f'{api_common.get_server_url()}/status',
                             json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
def endpoints(cluster: str, port: Optional[Union[int, str]] = None) -> str:
    body = payloads.EndpointBody(
        cluster=cluster,
        port=port,
    )
    response = requests.post(f'{api_common.get_server_url()}/endpoints',
                             json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
def cost_report() -> str:  # pylint: disable=redefined-builtin
    response = requests.get(f'{api_common.get_server_url()}/cost_report')
    return api_common.get_request_id(response)


# === Storage APIs ===
@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
def storage_ls() -> str:
    response = requests.get(f'{api_common.get_server_url()}/storage/ls')
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
def storage_delete(name: str) -> str:
    body = payloads.StorageBody(name=name)
    response = requests.post(f'{api_common.get_server_url()}/storage/delete',
                             json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
def local_up(gpus: bool) -> str:
    body = payloads.LocalUpBody(gpus=gpus)
    response = requests.post(f'{api_common.get_server_url()}/local_up',
                             json=json.loads(body.model_dump_json()))
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
def local_down() -> str:
    response = requests.post(f'{api_common.get_server_url()}/local_down')
    return api_common.get_request_id(response)


# === API request API ===


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
def get(request_id: str) -> Any:
    response = requests.get(
        f'{api_common.get_server_url()}/get?request_id={request_id}',
        timeout=(5, None))
    if response.status_code != 200:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(f'Failed to get request {request_id}: '
                               f'{response.status_code} {response.text}')
    request_task = requests_lib.Request.decode(
        requests_lib.RequestPayload(**response.json()))
    error = request_task.get_error()
    if error is not None:
        error_obj = error['object']
        if env_options.Options.SHOW_DEBUG_INFO.get():
            stacktrace = getattr(error_obj, 'stacktrace', str(error_obj))
            logger.error('=== Traceback on SkyPilot API Server ===\n'
                         f'{stacktrace}')
        with ux_utils.print_exception_no_traceback():
            raise error_obj
    return request_task.get_return_value()


@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
def stream_and_get(request_id: Optional[str] = None,
                   log_path: Optional[str] = None) -> Any:
    """Stream the logs of a request and get the final result.

    This will block until the request is finished. The request id can be a
    prefix of the full request id.
    """
    body = payloads.StreamBody(
        request_id=request_id,
        log_path=log_path,
        plain_logs=False,
    )
    response = requests.get(
        f'{api_common.get_server_url()}/stream',
        params=api_common.request_body_to_params(body),
        # 5 seconds to connect, no read timeout
        timeout=(5, None),
        stream=True)

    if response.status_code != 200:
        return get(request_id)
    try:
        for line in response.iter_lines():
            if line:
                msg = line.decode('utf-8')
                msg = rich_utils.decode_rich_status(msg)
                if msg is not None:
                    print(msg, flush=True)
        return get(request_id)
    except Exception:  # pylint: disable=broad-except
        logger.debug(f'Check more loggings with: sky api get {request_id}')
        raise


# === API server management ===
@usage_lib.entrypoint
@api_common.check_health
@annotations.public_api
def api_start(*, api_server_reload: bool = False, deploy: bool = False):
    """Start the API server."""
    # Only used in api_common.check_health, this is to satisfy the type checker.
    del api_server_reload, deploy
    is_local_api_server = api_common.is_api_server_local()
    prefix_symbol = (ux_utils.INDENT_SYMBOL
                     if is_local_api_server else ux_utils.INDENT_LAST_SYMBOL)
    logger.info(
        f'{prefix_symbol}SkyPilot API server: {api_common.get_server_url()}')
    if is_local_api_server:
        logger.info(ux_utils.INDENT_LAST_SYMBOL +
                    f'View API server logs at: {constants.API_SERVER_LOGS}')
        return


@usage_lib.entrypoint
@annotations.public_api
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
            subprocess_utils.kill_children_processes(parent_pids=[process.pid],
                                                     force=True)
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
@annotations.public_api
def api_server_logs(follow: bool = True, tail: str = 'all'):
    """Stream the API server logs."""
    if api_common.is_api_server_local():
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
    else:
        stream_and_get(log_path=constants.API_SERVER_LOGS)


@usage_lib.entrypoint
@annotations.public_api
def abort(request_id: Optional[str] = None, all: bool = False) -> str:  # pylint: disable=redefined-builtin
    """Abort a request or all requests."""
    body = payloads.RequestIdBody(request_id=request_id, all=all)
    print(f'Sending abort request to API server for {request_id}')
    response = requests.post(f'{api_common.get_server_url()}/abort',
                             json=json.loads(body.model_dump_json()),
                             timeout=5)
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@annotations.public_api
def requests_ls(
    request_id: Optional[str] = None,
    # pylint: disable=redefined-builtin
    all: bool = False
) -> List[requests_lib.RequestPayload]:
    body = payloads.RequestIdBody(request_id=request_id, all=all)
    response = requests.get(f'{api_common.get_server_url()}/requests',
                            params=api_common.request_body_to_params(body),
                            timeout=5)
    api_common.handle_request_error(response)
    return [
        requests_lib.RequestPayload(**request) for request in response.json()
    ]
