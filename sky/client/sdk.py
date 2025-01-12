"""Client-side Python SDK for SkyPilot.

All functions will return a future that can be awaited on with the `get` method.

Usage example:

.. code-block:: python

    request_id = sky.status()
    statuses = sky.get(request_id)

"""
import json
import logging
import os
import pathlib
import subprocess
import tempfile
import typing
from typing import Any, Dict, List, Optional, Tuple, Union
import zipfile

import click
import colorama
import filelock
import psutil
import requests

from sky import backends
from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.backends import backend_utils
from sky.server import common as server_common
from sky.server import constants as server_constants
from sky.server.requests import payloads
from sky.server.requests import requests as requests_lib
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import annotations
from sky.utils import common
from sky.utils import common_utils
from sky.utils import dag_utils
from sky.utils import env_options
from sky.utils import rich_utils
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import sky

logger = sky_logging.init_logger(__name__)
logging.getLogger('httpx').setLevel(logging.CRITICAL)


def _stream_response(request_id: Optional[str],
                     response: requests.Response) -> Any:
    """Streams the response to the console."""
    try:
        for line in rich_utils.decode_rich_status(response):
            if line is not None:
                print(line, flush=True)
        return get(request_id)
    except Exception:  # pylint: disable=broad-except
        logger.debug(f'To stream request logs: sky api logs {request_id}')
        raise


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def check(clouds: Optional[Tuple[str]],
          verbose: bool) -> server_common.RequestId:
    """Checks the credentials to enable clouds.

    Args:
        clouds: The clouds to check.
        verbose: Whether to show verbose output.

    Returns:
        The request ID of the check request.
    """
    body = payloads.CheckBody(clouds=clouds, verbose=verbose)
    response = requests.post(f'{server_common.get_server_url()}/check',
                             json=json.loads(body.model_dump_json()))
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def enabled_clouds() -> server_common.RequestId:
    """Gets the enabled clouds.

    Returns:
        request_id: The request ID of the enabled clouds request.
    """
    response = requests.get(f'{server_common.get_server_url()}/enabled_clouds')
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def list_accelerators(gpus_only: bool = True,
                      name_filter: Optional[str] = None,
                      region_filter: Optional[str] = None,
                      quantity_filter: Optional[int] = None,
                      clouds: Optional[Union[List[str], str]] = None,
                      all_regions: bool = False,
                      require_price: bool = True,
                      case_sensitive: bool = True) -> server_common.RequestId:
    """Lists the names of all accelerators offered by Sky.

    This will include all accelerators offered by Sky, including those
    that may not be available in the user's account.

    Args:
        gpus_only: Whether to only list GPU accelerators.
        name_filter: The name filter.
        region_filter: The region filter.
        quantity_filter: The quantity filter.
        clouds: The clouds to list.
        all_regions: Whether to list all regions.
        require_price: Whether to require price.
        case_sensitive: Whether to case sensitive.

    Returns:
        The request ID of the list accelerator counts request.

    Request Returns:
        A dictionary of canonical accelerator names mapped to a list
        of instance type offerings. See usage in cli.py.
    """
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
    response = requests.post(
        f'{server_common.get_server_url()}/list_accelerators',
        json=json.loads(body.model_dump_json()))
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def list_accelerator_counts(
        gpus_only: bool = True,
        name_filter: Optional[str] = None,
        region_filter: Optional[str] = None,
        quantity_filter: Optional[int] = None,
        clouds: Optional[Union[List[str],
                               str]] = None) -> server_common.RequestId:
    """Lists all accelerators offered by Sky and available counts.

    Args:
        gpus_only: Whether to only list GPU accelerators.
        name_filter: The name filter.
        region_filter: The region filter.
        quantity_filter: The quantity filter.
        clouds: The clouds to list.

    Returns:
        The request ID of the list accelerator counts request.

    Request Returns:
        A dictionary of canonical accelerator names mapped to a list
        of available counts. See usage in cli.py.
    """
    body = payloads.ListAcceleratorsBody(
        gpus_only=gpus_only,
        name_filter=name_filter,
        region_filter=region_filter,
        quantity_filter=quantity_filter,
        clouds=clouds,
    )
    response = requests.post(
        f'{server_common.get_server_url()}/list_accelerator_counts',
        json=json.loads(body.model_dump_json()))
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def optimize(
    dag: 'sky.Dag',
    minimize: common.OptimizeTarget = common.OptimizeTarget.COST
) -> server_common.RequestId:
    """Finds the best execution plan for the given DAG.

    Args:
        dag: the DAG to optimize.
        minimize: whether to minimize cost or time.

    Returns:
        request_id: The request ID of the optimize request.

    Raises:
        exceptions.ResourcesUnavailableError: if no resources are available
            for a task.
        exceptions.NoCloudAccessError: if no public clouds are enabled.
    """
    dag_str = dag_utils.dump_chain_dag_to_yaml_str(dag)

    body = payloads.OptimizeBody(dag=dag_str, minimize=minimize)
    response = requests.post(f'{server_common.get_server_url()}/optimize',
                             json=json.loads(body.model_dump_json()))
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def validate(dag: 'sky.Dag',
             workdir_only: bool = False) -> server_common.RequestId:
    """Validates the tasks.

    The file paths (workdir and file_mounts) are validated on the client side
    while the rest (e.g. resource) are validated on server side.

    Args:
        dag: the DAG to validate.
        workdir_only: whether to only validate the workdir. This is used for
            `exec` as it does not need other files/folders in file_mounts.

    Returns:
        request_id: The request ID of the validate request.
    """
    for task in dag.tasks:
        task.expand_and_validate_workdir()
        if not workdir_only:
            task.expand_and_validate_file_mounts()
    dag_str = dag_utils.dump_chain_dag_to_yaml_str(dag)
    body = payloads.ValidateBody(dag=dag_str)
    response = requests.post(f'{server_common.get_server_url()}/validate',
                             json=json.loads(body.model_dump_json()))
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
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
) -> server_common.RequestId:
    """Launches a cluster or task.

    The task's setup and run commands are executed under the task's workdir
    (when specified, it is synced to remote cluster).  The task undergoes job
    queue scheduling on the cluster.

    Currently, the first argument must be a sky.Task, or (EXPERIMENTAL advanced
    usage) a sky.Dag. In the latter case, currently it must contain a single
    task; support for pipelines/general DAGs are in experimental branches.

    Example:
        .. code-block:: python

            import sky
            task = sky.Task(run='echo hello SkyPilot')
            task.set_resources(
                sky.Resources(cloud=sky.AWS(), accelerators='V100:4'))
            sky.launch(task, cluster_name='my-cluster')


    Args:
        task: sky.Task, or sky.Dag (experimental; 1-task only) to launch.
        cluster_name: name of the cluster to create/reuse.  If None,
            auto-generate a name.
        retry_until_up: whether to retry launching the cluster until it is
            up.
        idle_minutes_to_autostop: automatically stop the cluster after this
            many minute of idleness, i.e., no running or pending jobs in the
            cluster's job queue. Idleness gets reset whenever setting-up/
            running/pending jobs are found in the job queue. Setting this
            flag is equivalent to running
            ``sky.launch(..., detach_run=True, ...)`` and then
            ``sky.autostop(idle_minutes=<minutes>)``. If not set, the cluster
            will not be autostopped.
        dryrun: if True, do not actually launch the cluster.
        down: Tear down the cluster after all jobs finish (successfully or
            abnormally). If --idle-minutes-to-autostop is also set, the
            cluster will be torn down after the specified idle time.
            Note that if errors occur during provisioning/data syncing/setting
            up, the cluster will not be torn down for debugging purposes.
        backend: backend to use.  If None, use the default backend
            (CloudVMRayBackend).
        optimize_target: target to optimize for. Choices: OptimizeTarget.COST,
            OptimizeTarget.TIME.
        no_setup: if True, do not re-run setup commands.
        clone_disk_from: [Experimental] if set, clone the disk from the
            specified cluster. This is useful to migrate the cluster to a
            different availability zone or region.
        fast: [Experimental] If the cluster is already up and available,
            skip provisioning and setup steps.
        need_confirmation: if True, show the confirmation prompt.

    Returns:
        request_id: The request ID of the launch request.

    Raises:
        exceptions.ClusterOwnerIdentityMismatchError: if the cluster is
            owned by another user.
        exceptions.InvalidClusterNameError: if the cluster name is invalid.
        exceptions.ResourcesMismatchError: if the requested resources
            do not match the existing cluster.
        exceptions.NotSupportedError: if required features are not supported
            by the backend/cloud/cluster.
        exceptions.ResourcesUnavailableError: if the requested resources
            cannot be satisfied. The failover_history of the exception
            will be set as:
                1. Empty: iff the first-ever sky.optimize() fails to
                find a feasible resource; no pre-check or actual launch is
                attempted.
                2. Non-empty: iff at least 1 exception from either
                our pre-checks (e.g., cluster name invalid) or a region/zone
                throwing resource unavailability.
        exceptions.CommandError: any ssh command error.
        exceptions.NoCloudAccessError: if all clouds are disabled.
    Other exceptions may be raised depending on the backend.

    Request Returns:
      job_id: Optional[int]; the job ID of the submitted job. None if the
        backend is not CloudVmRayBackend, or no job is submitted to
        the cluster.
      handle: Optional[backends.ResourceHandle]; the handle to the cluster. None
        if dryrun.
    """
    if cluster_name is None:
        cluster_name = backend_utils.generate_cluster_name()

    # TODO(zhwu): implement clone_disk_from
    # clone_source_str = ''
    # if clone_disk_from is not None:
    #     clone_source_str = f' from the disk of {clone_disk_from!r}'
    #     task, _ = backend_utils.check_can_clone_disk_and_override_task(
    #         clone_disk_from, cluster_name, task)
    dag = dag_utils.convert_entrypoint_to_dag(task)
    validate(dag)

    confirm_shown = False
    if need_confirmation:
        cluster_status = None
        request_id = status([cluster_name], all_users=True)
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

    dag = server_common.upload_mounts_to_api_server(dag)

    dag_str = dag_utils.dump_chain_dag_to_yaml_str(dag)

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
        f'{server_common.get_server_url()}/launch',
        json=json.loads(body.model_dump_json()),
        timeout=5,
    )
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def exec(  # pylint: disable=redefined-builtin
    task: Union['sky.Task', 'sky.Dag'],
    cluster_name: Optional[str] = None,
    dryrun: bool = False,
    down: bool = False,  # pylint: disable=redefined-outer-name
    backend: Optional[backends.Backend] = None,
) -> server_common.RequestId:
    """Executes a task on an existing cluster.

    This function performs two actions:

    (1) workdir syncing, if the task has a workdir specified;
    (2) executing the task's ``run`` commands.

    All other steps (provisioning, setup commands, file mounts syncing) are
    skipped.  If any of those specifications changed in the task, this function
    will not reflect those changes.  To ensure a cluster's setup is up to date,
    use ``sky.launch()`` instead.

    Execution and scheduling behavior:

    - The task will undergo job queue scheduling, respecting any specified
      resource requirement. It can be executed on any node of the cluster with
      enough resources.
    - The task is run under the workdir (if specified).
    - The task is run non-interactively (without a pseudo-terminal or
      pty), so interactive commands such as ``htop`` do not work.
      Use ``ssh my_cluster`` instead.

    Args:
        task: sky.Task, or sky.Dag (experimental; 1-task only) containing the
          task to execute.
        cluster_name: name of an existing cluster to execute the task.
        dryrun: if True, do not actually execute the task.
        down: Tear down the cluster after all jobs finish (successfully or
            abnormally). If --idle-minutes-to-autostop is also set, the
            cluster will be torn down after the specified idle time.
            Note that if errors occur during provisioning/data syncing/setting
            up, the cluster will not be torn down for debugging purposes.
        backend: backend to use.  If None, use the default backend
            (CloudVMRayBackend).

    Returns:
        request_id: The request ID of the exec request.

    Raises:
        ValueError: if the specified cluster is not in UP status.
        sky.exceptions.ClusterDoesNotExist: if the specified cluster does not
            exist.
        sky.exceptions.NotSupportedError: if the specified cluster is a
            controller that does not support this operation.

    Request Returns:
      job_id: Optional[int]; the job ID of the submitted job. None if the
        backend is not CloudVmRayBackend, or no job is submitted to
        the cluster.
      handle: Optional[backends.ResourceHandle]; the handle to the cluster. None
        if dryrun.
    """
    dag = dag_utils.convert_entrypoint_to_dag(task)
    validate(dag, workdir_only=True)
    dag = server_common.upload_mounts_to_api_server(dag, workdir_only=True)
    dag_str = dag_utils.dump_chain_dag_to_yaml_str(dag)
    body = payloads.ExecBody(
        task=dag_str,
        cluster_name=cluster_name,
        dryrun=dryrun,
        down=down,
        backend=backend.NAME if backend else None,
    )

    response = requests.post(
        f'{server_common.get_server_url()}/exec',
        json=json.loads(body.model_dump_json()),
        timeout=5,
    )
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def tail_logs(cluster_name: str,
              job_id: Optional[int],
              follow: bool,
              tail: int = 0) -> server_common.RequestId:
    """Tails the logs of a job.

    Args:
        cluster_name: name of the cluster.
        job_id: job id.
        follow: if True, follow the logs. Otherwise, return the logs
            immediately.
        tail: if > 0, tail the last N lines of the logs.

    Raises:
        ValueError: if arguments are invalid or the cluster is not supported.
        sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
        sky.exceptions.ClusterNotUpError: if the cluster is not UP.
        sky.exceptions.NotSupportedError: if the cluster is not based on
          CloudVmRayBackend.
        sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
          not the same as the user who created the cluster.
        sky.exceptions.CloudUserIdentityError: if we fail to get the current
          user identity.
    """
    body = payloads.ClusterJobBody(
        cluster_name=cluster_name,
        job_id=job_id,
        follow=follow,
        tail=tail,
    )
    response = requests.post(f'{server_common.get_server_url()}/logs',
                             json=json.loads(body.model_dump_json()),
                             stream=True,
                             timeout=(5, None))
    request_id = server_common.get_request_id(response)
    return _stream_response(request_id, response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def download_logs(cluster_name: str,
                  job_ids: Optional[List[str]]) -> Dict[str, str]:
    """Downloads the logs of jobs.

    Args:
        cluster_name: (str) name of the cluster.
        job_ids: (List[str]) job ids.

    Returns:
        request_id: The request ID of the download_logs request.

    Request Returns:
        Dict[str, str]: a mapping of job_id to local log path.

    Raises:
        sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
        sky.exceptions.ClusterNotUpError: if the cluster is not UP.
        sky.exceptions.NotSupportedError: if the cluster is not based on
          CloudVmRayBackend.
        sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
          not the same as the user who created the cluster.
        sky.exceptions.CloudUserIdentityError: if we fail to get the current
          user identity.
    """
    body = payloads.ClusterJobsBody(
        cluster_name=cluster_name,
        job_ids=job_ids,
    )
    response = requests.post(f'{server_common.get_server_url()}/download_logs',
                             json=json.loads(body.model_dump_json()))
    remote_path_dict = stream_and_get(server_common.get_request_id(response))
    remote2local_path_dict = {
        remote_path: remote_path.replace(
            str(server_common.api_server_user_logs_dir_prefix()),
            constants.SKY_LOGS_DIRECTORY)
        for remote_path in remote_path_dict.values()
    }
    body = payloads.DownloadBody(folder_paths=list(remote_path_dict.values()),)
    response = requests.post(f'{server_common.get_server_url()}/download',
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
@server_common.check_server_healthy_or_start
@annotations.public_api
def start(
    cluster_name: str,
    idle_minutes_to_autostop: Optional[int] = None,
    retry_until_up: bool = False,
    down: bool = False,  # pylint: disable=redefined-outer-name
    force: bool = False,
) -> server_common.RequestId:
    """Restart a cluster.

    If a cluster is previously stopped (status is STOPPED) or failed in
    provisioning/runtime installation (status is INIT), this function will
    attempt to start the cluster.  In the latter case, provisioning and runtime
    installation will be retried.

    Auto-failover provisioning is not used when restarting a stopped
    cluster. It will be started on the same cloud, region, and zone that were
    chosen before.

    If a cluster is already in the UP status, this function has no effect.

    Args:
        cluster_name: name of the cluster to start.
        idle_minutes_to_autostop: automatically stop the cluster after this
            many minute of idleness, i.e., no running or pending jobs in the
            cluster's job queue. Idleness gets reset whenever setting-up/
            running/pending jobs are found in the job queue. Setting this
            flag is equivalent to running
            ``sky.launch(..., detach_run=True, ...)`` and then
            ``sky.autostop(idle_minutes=<minutes>)``. If not set, the
            cluster will not be autostopped.
        retry_until_up: whether to retry launching the cluster until it is
            up.
        down: Autodown the cluster: tear down the cluster after specified
            minutes of idle time after all jobs finish (successfully or
            abnormally). Requires ``idle_minutes_to_autostop`` to be set.
        force: whether to force start the cluster even if it is already up.
            Useful for upgrading SkyPilot runtime.

    Returns:
        request_id: The request ID of the start request.

    Raises:
        ValueError: argument values are invalid: (1) if ``down`` is set to True
          but ``idle_minutes_to_autostop`` is None; (2) if the specified
          cluster is the managed jobs controller, and either
          ``idle_minutes_to_autostop`` is not None or ``down`` is True (omit
          them to use the default autostop settings).
        sky.exceptions.ClusterDoesNotExist: the specified cluster does not
          exist.
        sky.exceptions.NotSupportedError: if the cluster to restart was
          launched using a non-default backend that does not support this
          operation.
        sky.exceptions.ClusterOwnerIdentitiesMismatchError: if the cluster to
            restart was launched by a different user.
    """
    body = payloads.StartBody(
        cluster_name=cluster_name,
        idle_minutes_to_autostop=idle_minutes_to_autostop,
        retry_until_up=retry_until_up,
        down=down,
        force=force,
    )
    response = requests.post(
        f'{server_common.get_server_url()}/start',
        json=json.loads(body.model_dump_json()),
        timeout=5,
    )
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def down(cluster_name: str, purge: bool = False) -> server_common.RequestId:
    """Tears down a cluster.

    Tearing down a cluster will delete all associated resources (all billing
    stops), and any data on the attached disks will be lost.  Accelerators
    (e.g., TPUs) that are part of the cluster will be deleted too.

    Args:
        cluster_name: name of the cluster to down.
        purge: (Advanced) Forcefully remove the cluster from SkyPilot's cluster
            table, even if the actual cluster termination failed on the cloud.
            WARNING: This flag should only be set sparingly in certain manual
            troubleshooting scenarios; with it set, it is the user's
            responsibility to ensure there are no leaked instances and related
            resources.

    Returns:
        request_id: The request ID of the down request.

    Raises:
        sky.exceptions.ClusterDoesNotExist: the specified cluster does not
          exist.
        RuntimeError: failed to tear down the cluster.
        sky.exceptions.NotSupportedError: the specified cluster is the managed
          jobs controller.
    """
    body = payloads.StopOrDownBody(
        cluster_name=cluster_name,
        purge=purge,
    )
    response = requests.post(
        f'{server_common.get_server_url()}/down',
        json=json.loads(body.model_dump_json()),
        timeout=5,
    )
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def stop(cluster_name: str, purge: bool = False) -> server_common.RequestId:
    """Stops a cluster.

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

    Returns:
        request_id: The request ID of the stop request.

    Raises:
        sky.exceptions.ClusterDoesNotExist: the specified cluster does not
          exist.
        RuntimeError: failed to stop the cluster.
        sky.exceptions.NotSupportedError: if the specified cluster is a spot
          cluster, or a TPU VM Pod cluster, or the managed jobs controller.
    """
    body = payloads.StopOrDownBody(
        cluster_name=cluster_name,
        purge=purge,
    )
    response = requests.post(
        f'{server_common.get_server_url()}/stop',
        json=json.loads(body.model_dump_json()),
        timeout=5,
    )
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def autostop(
    cluster_name: str,
    idle_minutes: int,
    down: bool = False  # pylint: disable=redefined-outer-name
) -> server_common.RequestId:
    """Schedules an autostop/autodown for a cluster.

    Autostop/autodown will automatically stop or teardown a cluster when it
    becomes idle for a specified duration.  Idleness means there are no
    in-progress (pending/running) jobs in a cluster's job queue.

    Idleness time of a cluster is reset to zero, whenever:

    - A job is submitted (``sky.launch()`` or ``sky.exec()``).

    - The cluster has restarted.

    - An autostop is set when there is no active setting. (Namely, either
      there's never any autostop setting set, or the previous autostop setting
      was canceled.) This is useful for restarting the autostop timer.

    Example: say a cluster without any autostop set has been idle for 1 hour,
    then an autostop of 30 minutes is set. The cluster will not be immediately
    autostopped. Instead, the idleness timer only starts counting after the
    autostop setting was set.

    When multiple autostop settings are specified for the same cluster, the
    last setting takes precedence.

    Args:
        cluster_name: name of the cluster.
        idle_minutes: the number of minutes of idleness (no pending/running
          jobs) after which the cluster will be stopped automatically. Setting
          to a negative number cancels any autostop/autodown setting.
        down: if true, use autodown (tear down the cluster; non-restartable),
          rather than autostop (restartable).

    Returns:
        request_id: The request ID of the autostop request.

    Raises:
        sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
        sky.exceptions.ClusterNotUpError: if the cluster is not UP.
        sky.exceptions.NotSupportedError: if the cluster is not based on
          CloudVmRayBackend or the cluster is TPU VM Pod.
        sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
          not the same as the user who created the cluster.
        sky.exceptions.CloudUserIdentityError: if we fail to get the current
          user identity.
    """
    body = payloads.AutostopBody(
        cluster_name=cluster_name,
        idle_minutes=idle_minutes,
        down=down,
    )
    response = requests.post(
        f'{server_common.get_server_url()}/autostop',
        json=json.loads(body.model_dump_json()),
        timeout=5,
    )
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def queue(cluster_name: List[str],
          skip_finished: bool = False,
          all_users: bool = False) -> server_common.RequestId:
    """Gets the job queue of a cluster.

    Args:
        cluster_name: name of the cluster.
        skip_finished: if True, skip finished jobs.
        all_users: if True, return jobs from all users.

    Returns:
        request_id: The request ID of the queue request.

    Request Returns:
        .. code-block:: python

            [
                {
                    'job_id': (int) job id,
                    'job_name': (str) job name,
                    'username': (str) username,
                    'user_hash': (str) user hash,
                    'submitted_at': (int) timestamp of submitted,
                    'start_at': (int) timestamp of started,
                    'end_at': (int) timestamp of ended,
                    'resources': (str) resources,
                    'status': (job_lib.JobStatus) job status,
                    'log_path': (str) log path,
                }
            ]

    raises:
        sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
        sky.exceptions.ClusterNotUpError: if the cluster is not UP.
        sky.exceptions.NotSupportedError: if the cluster is not based on
          CloudVmRayBackend.
        sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
          not the same as the user who created the cluster.
        sky.exceptions.CloudUserIdentityError: if we fail to get the current
          user identity.
        exceptions.CommandError: if failed to get the job queue with ssh.
    """
    body = payloads.QueueBody(
        cluster_name=cluster_name,
        skip_finished=skip_finished,
        all_users=all_users,
    )
    response = requests.post(f'{server_common.get_server_url()}/queue',
                             json=json.loads(body.model_dump_json()))
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def job_status(cluster_name: str,
               job_ids: Optional[List[int]] = None) -> server_common.RequestId:
    # TODO: merge this into the queue endpoint, i.e., let the queue endpoint
    # take job_ids to filter the returned jobs.
    body = payloads.JobStatusBody(
        cluster_name=cluster_name,
        job_ids=job_ids,
    )
    response = requests.post(f'{server_common.get_server_url()}/job_status',
                             json=json.loads(body.model_dump_json()))
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def cancel(
    cluster_name: str,
    all: bool = False,  # pylint: disable=redefined-builtin
    all_users: bool = False,
    job_ids: Optional[List[int]] = None,
    # pylint: disable=invalid-name
    _try_cancel_if_cluster_is_init: bool = False
) -> server_common.RequestId:
    """Cancels jobs on a cluster.

    Args:
        cluster_name: name of the cluster.
        all: if True, cancel all jobs.
        all_users: if True, cancel all jobs from all users.
        job_ids: a list of job IDs to cancel.
        _try_cancel_if_cluster_is_init: (bool) whether to try cancelling the job
            even if the cluster is not UP, but the head node is still alive.
            This is used by the jobs controller to cancel the job when the
            worker node is preempted in the spot cluster.

    Returns:
        request_id: The request ID of the cancel request.

    Raises:
        ValueError: if arguments are invalid.
        sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
        sky.exceptions.ClusterNotUpError: if the cluster is not UP.
        sky.exceptions.NotSupportedError: if the specified cluster is a
          controller that does not support this operation.
        sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
          not the same as the user who created the cluster.
        sky.exceptions.CloudUserIdentityError: if we fail to get the current
          user identity.
    """
    body = payloads.CancelBody(
        cluster_name=cluster_name,
        all=all,
        all_users=all_users,
        job_ids=job_ids,
        try_cancel_if_cluster_is_init=_try_cancel_if_cluster_is_init,
    )
    response = requests.post(f'{server_common.get_server_url()}/cancel',
                             json=json.loads(body.model_dump_json()))
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def status(
    cluster_names: Optional[List[str]] = None,
    refresh: common.StatusRefreshMode = common.StatusRefreshMode.NONE,
    all_users: bool = False,
) -> server_common.RequestId:
    """Gets cluster statuses.

    If cluster_names is given, return those clusters. Otherwise, return all
    clusters.

    Each returned value has the following fields:

    .. code-block:: python

        {
            'name': (str) cluster name,
            'launched_at': (int) timestamp of last launch on this cluster,
            'handle': (ResourceHandle) an internal handle to the cluster,
            'last_use': (str) the last command/entrypoint that affected this
              cluster,
            'status': (sky.ClusterStatus) cluster status,
            'autostop': (int) idle time before autostop,
            'to_down': (bool) whether autodown is used instead of autostop,
            'metadata': (dict) metadata of the cluster,
            'user_hash': (str) user hash of the cluster owner,
            'user_name': (str) user name of the cluster owner,
        }

    Each cluster can have one of the following statuses:

    - ``INIT``: The cluster may be live or down. It can happen in the following
      cases:

      - Ongoing provisioning or runtime setup. (A ``sky.launch()`` has started
        but has not completed.)
      - Or, the cluster is in an abnormal state, e.g., some cluster nodes are
        down, or the SkyPilot runtime is unhealthy. (To recover the cluster,
        try ``sky launch`` again on it.)

    - ``UP``: Provisioning and runtime setup have succeeded and the cluster is
      live.  (The most recent ``sky.launch()`` has completed successfully.)

    - ``STOPPED``: The cluster is stopped and the storage is persisted. Use
      ``sky.start()`` to restart the cluster.

    Autostop column:

    - The autostop column indicates how long the cluster will be autostopped
      after minutes of idling (no jobs running). If ``to_down`` is True, the
      cluster will be autodowned, rather than autostopped.

    Getting up-to-date cluster statuses:

    - In normal cases where clusters are entirely managed by SkyPilot (i.e., no
      manual operations in cloud consoles) and no autostopping is used, the
      table returned by this command will accurately reflect the cluster
      statuses.

    - In cases where the clusters are changed outside of SkyPilot (e.g., manual
      operations in cloud consoles; unmanaged spot clusters getting preempted)
      or for autostop-enabled clusters, use ``refresh=True`` to query the
      latest cluster statuses from the cloud providers.

    Args:
        cluster_names: a list of cluster names to query. If not
            provided, all clusters will be queried.
        refresh: whether to query the latest cluster statuses from the cloud
            provider(s).
        all_users: whether to include all users' clusters. By default, only
            the current user's clusters are included.

    Request Returns:
        A list of dicts, with each dict containing the information of a
        cluster. If a cluster is found to be terminated or not found, it will
        be omitted from the returned list.

    Returns:
        request_id: The request ID of the status request.
    """
    # TODO(zhwu): this does not stream the logs output by logger back to the
    # user, due to the rich progress implementation.
    body = payloads.StatusBody(
        cluster_names=cluster_names,
        refresh=refresh,
        all_users=all_users,
    )
    response = requests.post(f'{server_common.get_server_url()}/status',
                             json=json.loads(body.model_dump_json()))
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def endpoints(
        cluster: str,
        port: Optional[Union[int, str]] = None) -> server_common.RequestId:
    """Gets the endpoint for a given cluster and port number (endpoint).

    Args:
        cluster: The name of the cluster.
        port: The port number to get the endpoint for. If None, endpoints
            for all ports are returned..

    Request Returns: A dictionary of port numbers to endpoints. If port is None,
        the dictionary will contain all ports:endpoints exposed on the cluster.

    RequestRaises:
        ValueError: if the cluster is not UP or the endpoint is not exposed.
        RuntimeError: if the cluster has no ports to be exposed or no endpoints
            are exposed yet.
    """
    body = payloads.EndpointsBody(
        cluster=cluster,
        port=port,
    )
    response = requests.post(f'{server_common.get_server_url()}/endpoints',
                             json=json.loads(body.model_dump_json()))
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def cost_report() -> server_common.RequestId:  # pylint: disable=redefined-builtin
    """Gets all cluster cost reports, including those that have been downed.

    Each returned value has the following fields:

    .. code-block:: python

        {
            'name': (str) cluster name,
            'launched_at': (int) timestamp of last launch on this cluster,
            'duration': (int) total seconds that cluster was up and running,
            'last_use': (str) the last command/entrypoint that affected this
            'num_nodes': (int) number of nodes launched for cluster,
            'resources': (resources.Resources) type of resource launched,
            'cluster_hash': (str) unique hash identifying cluster,
            'usage_intervals': (List[Tuple[int, int]]) cluster usage times,
            'total_cost': (float) cost given resources and usage intervals,
        }

    The estimated cost column indicates price for the cluster based on the type
    of resources being used and the duration of use up until the call to
    status. This means if the cluster is UP, successive calls to report will
    show increasing price. The estimated cost is calculated based on the local
    cache of the cluster status, and may not be accurate for the cluster with
    autostop/use_spot set or terminated/stopped on the cloud console.

    Request Returns:
        A list of dicts, with each dict containing the cost information of a
        cluster.

    Returns:
        request_id: The request ID of the cost report request.
    """
    response = requests.get(f'{server_common.get_server_url()}/cost_report')
    return server_common.get_request_id(response)


# === Storage APIs ===
@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def storage_ls() -> server_common.RequestId:
    """Gets the storages.

    Returns:
        request_id: The request ID of the storage list request.

    Request Returns:
        [
            {
                'name': str,
                'launched_at': int timestamp of creation,
                'store': List[sky.StoreType],
                'last_use': int timestamp of last use,
                'status': sky.StorageStatus,
            }
        ]
    """
    response = requests.get(f'{server_common.get_server_url()}/storage/ls')
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def storage_delete(name: str) -> server_common.RequestId:
    """Deletes a storage.

    Args:
        name: The name of the storage to delete.

    Returns:
        request_id: The request ID of the storage delete request.

    Request Raises:
        ValueError: If the storage does not exist.
    """
    body = payloads.StorageBody(name=name)
    response = requests.post(f'{server_common.get_server_url()}/storage/delete',
                             json=json.loads(body.model_dump_json()))
    return server_common.get_request_id(response)


# === Kubernetes ===


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def local_up(gpus: bool) -> server_common.RequestId:
    """Launches a Kubernetes cluster on local machines.

    Returns:
        request_id: The request ID of the local up request.
    """
    body = payloads.LocalUpBody(gpus=gpus)
    response = requests.post(f'{server_common.get_server_url()}/local_up',
                             json=json.loads(body.model_dump_json()))
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def local_down() -> server_common.RequestId:
    """Tears down the Kubernetes cluster started by local_up."""
    response = requests.post(f'{server_common.get_server_url()}/local_down')
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def realtime_kubernetes_gpu_availability(
        context: Optional[str] = None,
        name_filter: Optional[str] = None,
        quantity_filter: Optional[int] = None) -> server_common.RequestId:
    """Gets the real-time Kubernetes GPU availability.

    Returns:
        request_id: The request ID of the real-time Kubernetes GPU availability
            request.
    """
    body = payloads.RealtimeGpuAvailabilityRequestBody(
        context=context,
        name_filter=name_filter,
        quantity_filter=quantity_filter,
    )
    response = requests.post(
        f'{server_common.get_server_url()}/'
        'realtime_kubernetes_gpu_availability',
        json=json.loads(body.model_dump_json()))
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def kubernetes_node_info(
        context: Optional[str] = None) -> server_common.RequestId:
    """Gets the resource information for all the nodes in the cluster.

    Currently only GPU resources are supported. The function returns the total
    number of GPUs available on the node and the number of free GPUs on the
    node.

    If the user does not have sufficient permissions to list pods in all
    namespaces, the function will return free GPUs as -1.

    Args:
        context: The Kubernetes context. If None, the default context is used.

    Returns:
        The request ID of the Kubernetes node info request.

    Request Returns:
        Dict[str, KubernetesNodeInfo]: Dictionary containing the node name as
            key and the KubernetesNodeInfo object as value
    """
    body = payloads.KubernetesNodeInfoRequestBody(context=context)
    response = requests.post(
        f'{server_common.get_server_url()}/kubernetes_node_info',
        json=json.loads(body.model_dump_json()))
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def status_kubernetes() -> server_common.RequestId:
    """Gets all SkyPilot clusters and jobs in the Kubernetes cluster.

    Managed jobs and services are also included in the clusters returned.
    The caller must parse the controllers to identify which clusters are run
    as managed jobs or services.

    Returns:
        The request ID of the status request.

    Request Returns:
        A tuple containing:
        - all_clusters: List of KubernetesSkyPilotClusterInfoPayload with info
            for all clusters, including managed jobs, services and controllers.
        - unmanaged_clusters: List of KubernetesSkyPilotClusterInfoPayload with
            info for all clusters excluding managed jobs and services.
            Controllers are included.
        - all_jobs: List of managed jobs from all controllers. Each entry is a
            dictionary job info, see jobs.queue_from_kubernetes_pod for details.
        - context: Kubernetes context used to fetch the cluster information.
    """
    response = requests.get(
        f'{server_common.get_server_url()}/status_kubernetes')
    return server_common.get_request_id(response)


# === API request APIs ===
@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def get(request_id: str) -> Any:
    """Waits for and gets the result of a request.

    Args:
        request_id: The request ID of the request to get.

    Returns:
        The `Request Returns` of the specified request. See the documentation
        of the specific requests above for more details.

    Raises:
        It raises the same exceptions as the specific requests, see
        `Request Raises` in the documentation of the specific requests above.
    """
    response = requests.get(
        f'{server_common.get_server_url()}/api/get?request_id={request_id}',
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
    if request_task.status == requests_lib.RequestStatus.CANCELLED:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.RequestCancelled(
                f'{colorama.Fore.YELLOW}Current {request_task.name!r} request '
                f'({request_task.request_id}) is cancelled by another process.'
                f'{colorama.Style.RESET_ALL}')
    return request_task.get_return_value()


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def stream_and_get(request_id: Optional[str] = None,
                   log_path: Optional[str] = None,
                   tail: Optional[int] = None,
                   follow: bool = True) -> Any:
    """Streams the logs of a request or a log file and gets the final result.

    This will block until the request is finished. The request id can be a
    prefix of the full request id.

    Args:
        request_id: The prefix of the request ID of the request to stream.
        log_path: The path to the log file to stream.
        tail: The number of lines to show from the end of the logs.
            If None, show all logs.
        follow: Whether to follow the logs.

    Returns:
        The `Request Returns` of the specified request. See the documentation
        of the specific requests above for more details.

    Raises:
        It raises the same exceptions as the specific requests, see
        `Request Raises` in the documentation of the specific requests above.
    """
    params = {
        'request_id': request_id,
        'log_path': log_path,
        'tail': str(tail) if tail is not None else None,
        'follow': follow,
        'format': 'console',
    }
    response = requests.get(
        f'{server_common.get_server_url()}/api/stream',
        params=params,
        # 5 seconds to connect, no read timeout
        timeout=(5, None),
        stream=True)
    if response.status_code != 200:
        return get(request_id)
    return _stream_response(request_id, response)


@usage_lib.entrypoint
@annotations.public_api
def api_cancel(
        request_id: Optional[str] = None,
        all: bool = False,  # pylint: disable=redefined-builtin
        all_users: bool = False,
        silent: bool = False) -> server_common.RequestId:
    """Aborts a request or all requests.

    Args:
        request_id: The prefix of the request ID of the request to abort.
        all: Whether to abort all requests.
        all_users: Whether to abort all requests from all users.
        silent: Whether to suppress the output.

    Returns:
        The request ID of the abort request itself.

    Raises:
        click.BadParameter: If no request ID is specified and not all or
            all_users is not set.
    """
    # TODO(zhwu): support a list of request IDs.
    if request_id is None and not all and not all_users:
        raise click.BadParameter('Either specify a request ID or use '
                                 '--all / --all-users to abort all requests.')
    echo = logger.info if not silent else logger.debug
    user_id = None
    if not all_users:
        user_id = common_utils.get_user_hash()
    body = payloads.RequestIdBody(request_id=request_id,
                                  all=all or all_users,
                                  user_id=user_id)
    if all_users:
        echo('Cancelling everyone\'s requests...')
    else:
        if request_id is not None:
            echo(f'Cancelling request: {request_id!r}...')
        if all:
            echo('Cancelling all your requests...')
    response = requests.post(f'{server_common.get_server_url()}/api/cancel',
                             json=json.loads(body.model_dump_json()),
                             timeout=5)
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@annotations.public_api
def api_status(
    request_id: Optional[str] = None,
    # pylint: disable=redefined-builtin
    all: bool = False
) -> List[requests_lib.RequestPayload]:
    """Lists all requests.

    Args:
        request_id: The prefix of the request ID of the request to list.
        all: Whether to list all requests.

    Returns:
        A list of requests.
    """
    body = payloads.RequestIdBody(request_id=request_id, all=all)
    response = requests.get(f'{server_common.get_server_url()}/api/status',
                            params=server_common.request_body_to_params(body),
                            timeout=5)
    server_common.handle_request_error(response)
    return [
        requests_lib.RequestPayload(**request) for request in response.json()
    ]


# === API server management APIs ===
@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def api_info() -> Dict[str, str]:
    """Gets the server's status, commit and version.

    Returns:
        A dictionary containing the server's status, commit and version.

        .. code-block:: python

            {
                'status': 'healthy',
                'api_version': '1',
                'commit': 'abc1234567890',
                'version': '1.0.0',
            }

    """
    response = requests.get(f'{server_common.get_server_url()}/api/health')
    response.raise_for_status()
    return response.json()


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.public_api
def api_start(
    *,
    deploy: bool = False,
    # api_server_reload is deprecated, since it is not well tested.
    # TODO(zhwu): test it or remove it.
    api_server_reload: bool = False,
) -> None:
    """Starts the API server.

    It checks the existence of the API server and starts it if it does not
    exist.

    Args:
        deploy: Whether to deploy the API server, i.e. fully utilize the
            resources of the machine.
        api_server_reload: Whether to automatically reload the API server, when
            the code is changed. This is not well tested.
    """
    # Only used in server_common.check_server_healthy_or_start, this is to
    # satisfy the type checker.
    del api_server_reload, deploy
    is_local_api_server = server_common.is_api_server_local()
    prefix_symbol = (ux_utils.INDENT_SYMBOL
                     if is_local_api_server else ux_utils.INDENT_LAST_SYMBOL)
    logger.info(
        f'{prefix_symbol}SkyPilot API server: {server_common.get_server_url()}')
    if is_local_api_server:
        logger.info(ux_utils.INDENT_LAST_SYMBOL +
                    f'View API server logs at: {constants.API_SERVER_LOGS}')
        return


@usage_lib.entrypoint
@annotations.public_api
def api_stop() -> None:
    """Stops the API server.

    It will do nothing if the API server is remotely hosted.
    """
    # Kill the uvicorn process by name: uvicorn sky.server.server:app
    server_url = server_common.get_server_url()
    if not server_common.is_api_server_local():
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                f'Cannot kill the API server at {server_url} because it is not '
                f'the default SkyPilot API server started locally.')

    found = False
    for process in psutil.process_iter(attrs=['pid', 'cmdline']):
        cmdline = process.info['cmdline']
        if cmdline and server_common.API_SERVER_CMD in ' '.join(cmdline):
            subprocess_utils.kill_children_processes(parent_pids=[process.pid],
                                                     force=True)
            found = True

    # Remove the database for requests including any files starting with
    # api.constants.API_SERVER_REQUEST_DB_PATH
    db_path = os.path.expanduser(server_constants.API_SERVER_REQUEST_DB_PATH)
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
def api_server_logs(follow: bool = True, tail: Optional[int] = None) -> None:
    """Streams the API server logs.

    Args:
        follow: Whether to follow the logs.
        tail: the number of lines to show from the end of the logs.
            If None, show all logs.
    """
    if server_common.is_api_server_local():
        tail_args = ['-f'] if follow else []
        if tail is None:
            tail_args.extend(['-n', '+1'])
        else:
            tail_args.extend(['-n', f'{tail}'])
        log_path = os.path.expanduser(constants.API_SERVER_LOGS)
        subprocess.run(['tail', *tail_args, f'{log_path}'], check=False)
    else:
        stream_and_get(log_path=constants.API_SERVER_LOGS, tail=tail)


@usage_lib.entrypoint
@annotations.public_api
def api_login(endpoint: Optional[str] = None) -> None:
    """Logs into a SkyPilot API server.

    This sets the endpoint globally, i.e., all SkyPilot CLI and SDK calls will
    use this endpoint.

    Args:
        endpoint: The endpoint of the SkyPilot API server, e.g.,
            http://1.2.3.4:46580 or https://skypilot.mydomain.com.
    """
    # TODO(zhwu): this SDK sets global endpoint, which may not be the best
    # design as a user may expect this is only effective for the current
    # session. We should also consider supporting env var for specifying
    # endpoint.
    if endpoint is None:
        endpoint = click.prompt('Enter your SkyPilot API server endpoint')
    # Check endpoint is a valid URL
    if endpoint and not endpoint.startswith(
            'http://') and not endpoint.startswith('https://'):
        raise click.BadParameter('Endpoint must be a valid URL.')
    if not endpoint:
        endpoint = None

    config_path = pathlib.Path(skypilot_config.CONFIG_PATH).expanduser()
    with filelock.FileLock(config_path.with_suffix('.lock')):
        if not skypilot_config.loaded():
            config_path.touch()
            config = {'api_server': {'endpoint': endpoint}}
        else:
            config = skypilot_config.set_nested(('api_server', 'endpoint'),
                                                endpoint)
        common_utils.dump_yaml(str(config_path), config)
        click.secho(f'Logged in to SkyPilot API server at {endpoint}',
                    fg='green')
