"""Client-side Python SDK for SkyPilot.

All functions will return a future that can be awaited on with the `get` method.

Usage example:

.. code-block:: python

    request_id = sky.status()
    statuses = sky.get(request_id)

"""
import getpass
import json
import logging
import os
import pathlib
import subprocess
import typing
from typing import Any, Dict, List, Optional, Tuple, Union

import click
import colorama
import filelock

from sky import admin_policy
from sky import backends
from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common as adaptors_common
from sky.client import common as client_common
from sky.server import common as server_common
from sky.server.requests import payloads
from sky.server.requests import requests as requests_lib
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import annotations
from sky.utils import cluster_utils
from sky.utils import common
from sky.utils import common_utils
from sky.utils import dag_utils
from sky.utils import env_options
from sky.utils import rich_utils
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import io

    import psutil
    import requests

    import sky
else:
    psutil = adaptors_common.LazyImport('psutil')
    requests = adaptors_common.LazyImport('requests')

logger = sky_logging.init_logger(__name__)
logging.getLogger('httpx').setLevel(logging.CRITICAL)


def stream_response(request_id: Optional[str],
                    response: 'requests.Response',
                    output_stream: Optional['io.TextIOBase'] = None) -> Any:
    """Streams the response to the console.

    Args:
        request_id: The request ID.
        response: The HTTP response.
        output_stream: The output stream to write to. If None, print to the
            console.
    """

    try:
        for line in rich_utils.decode_rich_status(response):
            if line is not None:
                print(line, flush=True, end='', file=output_stream)
        return get(request_id)
    except Exception:  # pylint: disable=broad-except
        logger.debug(f'To stream request logs: sky api logs {request_id}')
        raise


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def check(clouds: Optional[Tuple[str]],
          verbose: bool) -> server_common.RequestId:
    """Checks the credentials to enable clouds.

    Args:
        clouds: The clouds to check.
        verbose: Whether to show verbose output.

    Returns:
        The request ID of the check request.

    Request Returns:
        None
    """
    body = payloads.CheckBody(clouds=clouds, verbose=verbose)
    response = requests.post(f'{server_common.get_server_url()}/check',
                             json=json.loads(body.model_dump_json()),
                             cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def enabled_clouds() -> server_common.RequestId:
    """Gets the enabled clouds.

    Returns:
        The request ID of the enabled clouds request.

    Request Returns:
        A list of enabled clouds in string format.
    """
    response = requests.get(f'{server_common.get_server_url()}/enabled_clouds',
                            cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
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
        acc_to_instance_type_dict (Dict[str, List[InstanceTypeInfo]]): A
            dictionary of canonical accelerator names mapped to a list of
            instance type offerings. See usage in cli.py.
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
        json=json.loads(body.model_dump_json()),
        cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
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
        acc_to_acc_num_dict (Dict[str, List[int]]): A dictionary of canonical
            accelerator names mapped to a list of available counts. See usage
            in cli.py.
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
        json=json.loads(body.model_dump_json()),
        cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def optimize(
    dag: 'sky.Dag',
    minimize: common.OptimizeTarget = common.OptimizeTarget.COST,
    admin_policy_request_options: Optional[admin_policy.RequestOptions] = None
) -> server_common.RequestId:
    """Finds the best execution plan for the given DAG.

    Args:
        dag: the DAG to optimize.
        minimize: whether to minimize cost or time.
        admin_policy_request_options: Request options used for admin policy
            validation. This is only required when a admin policy is in use,
            see: https://docs.skypilot.co/en/latest/cloud-setup/policy.html

    Returns:
        The request ID of the optimize request.

    Request Returns:
        optimized_dag (str): The optimized DAG in YAML format.

    Request Raises:
        exceptions.ResourcesUnavailableError: if no resources are available
            for a task.
        exceptions.NoCloudAccessError: if no public clouds are enabled.
    """
    dag_str = dag_utils.dump_chain_dag_to_yaml_str(dag)

    body = payloads.OptimizeBody(dag=dag_str,
                                 minimize=minimize,
                                 request_options=admin_policy_request_options)
    response = requests.post(f'{server_common.get_server_url()}/optimize',
                             json=json.loads(body.model_dump_json()),
                             cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def validate(
    dag: 'sky.Dag',
    workdir_only: bool = False,
    admin_policy_request_options: Optional[admin_policy.RequestOptions] = None
) -> None:
    """Validates the tasks.

    The file paths (workdir and file_mounts) are validated on the client side
    while the rest (e.g. resource) are validated on server side.

    Raises exceptions if the DAG is invalid.

    Args:
        dag: the DAG to validate.
        workdir_only: whether to only validate the workdir. This is used for
            `exec` as it does not need other files/folders in file_mounts.
        admin_policy_request_options: Request options used for admin policy
            validation. This is only required when a admin policy is in use,
            see: https://docs.skypilot.co/en/latest/cloud-setup/policy.html
    """
    for task in dag.tasks:
        task.expand_and_validate_workdir()
        if not workdir_only:
            task.expand_and_validate_file_mounts()
    dag_str = dag_utils.dump_chain_dag_to_yaml_str(dag)
    body = payloads.ValidateBody(dag=dag_str,
                                 request_options=admin_policy_request_options)
    response = requests.post(f'{server_common.get_server_url()}/validate',
                             json=json.loads(body.model_dump_json()),
                             cookies=server_common.get_api_cookie_jar())
    if response.status_code == 400:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.deserialize_exception(
                response.json().get('detail'))


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
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
    # Internal only:
    # pylint: disable=invalid-name
    _need_confirmation: bool = False,
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
          flag is equivalent to running ``sky.launch()`` and then
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
        _need_confirmation: (Internal only) If True, show the confirmation
            prompt.

    Returns:
        The request ID of the launch request.

    Request Returns:
        job_id (Optional[int]): the job ID of the submitted job. None if the
          backend is not ``CloudVmRayBackend``, or no job is submitted to the
          cluster.
        handle (Optional[backends.ResourceHandle]): the handle to the cluster.
          None if dryrun.

    Request Raises:
        exceptions.ClusterOwnerIdentityMismatchError: if the cluster is owned
          by another user.
        exceptions.InvalidClusterNameError: if the cluster name is invalid.
        exceptions.ResourcesMismatchError: if the requested resources
          do not match the existing cluster.
        exceptions.NotSupportedError: if required features are not supported
          by the backend/cloud/cluster.
        exceptions.ResourcesUnavailableError: if the requested resources
          cannot be satisfied. The failover_history of the exception will be set
          as:

          1. Empty: iff the first-ever sky.optimize() fails to find a feasible
             resource; no pre-check or actual launch is attempted.

          2. Non-empty: iff at least 1 exception from either our pre-checks
             (e.g., cluster name invalid) or a region/zone throwing resource
             unavailability.

        exceptions.CommandError: any ssh command error.
        exceptions.NoCloudAccessError: if all clouds are disabled.

    Other exceptions may be raised depending on the backend.
    """
    if cluster_name is None:
        cluster_name = cluster_utils.generate_cluster_name()

    if clone_disk_from is not None:
        with ux_utils.print_exception_no_traceback():
            raise NotImplementedError('clone_disk_from is not implemented yet. '
                                      'Please contact the SkyPilot team if you '
                                      'need this feature at slack.skypilot.co.')
    dag = dag_utils.convert_entrypoint_to_dag(task)
    request_options = admin_policy.RequestOptions(
        cluster_name=cluster_name,
        idle_minutes_to_autostop=idle_minutes_to_autostop,
        down=down,
        dryrun=dryrun)
    validate(dag, admin_policy_request_options=request_options)

    confirm_shown = False
    if _need_confirmation:
        cluster_status = None
        # TODO(SKY-998): we should reduce RTTs before launching the cluster.
        request_id = status([cluster_name], all_users=True)
        clusters = get(request_id)
        cluster_user_hash = common_utils.get_user_hash()
        cluster_user_hash_str = ''
        cluster_user_name = getpass.getuser()
        if not clusters:
            # Show the optimize log before the prompt if the cluster does not
            # exist.
            request_id = optimize(dag,
                                  admin_policy_request_options=request_options)
            stream_and_get(request_id)
        else:
            cluster_record = clusters[0]
            cluster_status = cluster_record['status']
            cluster_user_hash = cluster_record['user_hash']
            cluster_user_name = cluster_record['user_name']
            if cluster_user_name == getpass.getuser():
                # Only show the hash if the username is the same as the local
                # username, to avoid confusion.
                cluster_user_hash_str = f' (hash: {cluster_user_hash})'

        # Prompt if (1) --cluster is None, or (2) cluster doesn't exist, or (3)
        # it exists but is STOPPED.
        prompt = None
        if cluster_status is None:
            prompt = (
                f'Launching a new cluster {cluster_name!r}. '
                # '{clone_source_str}. '
                'Proceed?')
        elif cluster_status == status_lib.ClusterStatus.STOPPED:
            user_name_str = ''
            if cluster_user_hash != common_utils.get_user_hash():
                user_name_str = (' created by another user '
                                 f'{cluster_user_name!r}'
                                 f'{cluster_user_hash_str}')
            prompt = (f'Restarting the stopped cluster {cluster_name!r}'
                      f'{user_name_str}. Proceed?')
        elif cluster_user_hash != common_utils.get_user_hash():
            # Prompt if the cluster was created by a different user.
            prompt = (f'Cluster {cluster_name!r} was created by another user '
                      f'{cluster_user_name!r}{cluster_user_hash_str}. '
                      'Reusing the cluster. Proceed?')
        if prompt is not None:
            confirm_shown = True
            click.confirm(prompt, default=True, abort=True, show_default=True)

    if not confirm_shown:
        click.secho('Running on cluster: ', fg='cyan', nl=False)
        click.secho(cluster_name)

    dag = client_common.upload_mounts_to_api_server(dag)

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
        quiet_optimizer=_need_confirmation,
        is_launched_by_jobs_controller=_is_launched_by_jobs_controller,
        is_launched_by_sky_serve_controller=(
            _is_launched_by_sky_serve_controller),
        disable_controller_check=_disable_controller_check,
    )
    response = requests.post(
        f'{server_common.get_server_url()}/launch',
        json=json.loads(body.model_dump_json()),
        timeout=5,
        cookies=server_common.get_api_cookie_jar(),
    )
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
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
        The request ID of the exec request.


    Request Returns:
        job_id (Optional[int]): the job ID of the submitted job. None if the
          backend is not CloudVmRayBackend, or no job is submitted to
          the cluster.
        handle (Optional[backends.ResourceHandle]): the handle to the cluster.
          None if dryrun.

    Request Raises:
        ValueError: if the specified cluster is not in UP status.
        sky.exceptions.ClusterDoesNotExist: if the specified cluster does not
          exist.
        sky.exceptions.NotSupportedError: if the specified cluster is a
          controller that does not support this operation.
    """
    dag = dag_utils.convert_entrypoint_to_dag(task)
    validate(dag, workdir_only=True)
    dag = client_common.upload_mounts_to_api_server(dag, workdir_only=True)
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
        cookies=server_common.get_api_cookie_jar(),
    )
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def tail_logs(cluster_name: str,
              job_id: Optional[int],
              follow: bool,
              tail: int = 0,
              output_stream: Optional['io.TextIOBase'] = None) -> int:
    """Tails the logs of a job.

    Args:
        cluster_name: name of the cluster.
        job_id: job id.
        follow: if True, follow the logs. Otherwise, return the logs
            immediately.
        tail: if > 0, tail the last N lines of the logs.
        output_stream: the stream to write the logs to. If None, print to the
            console.

    Returns:
        Exit code based on success or failure of the job. 0 if success,
        100 if the job failed. See exceptions.JobExitCode for possible exit
        codes.

    Request Raises:
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
    response = requests.post(
        f'{server_common.get_server_url()}/logs',
        json=json.loads(body.model_dump_json()),
        stream=True,
        timeout=(client_common.API_SERVER_REQUEST_CONNECTION_TIMEOUT_SECONDS,
                 None),
        cookies=server_common.get_api_cookie_jar())
    request_id = server_common.get_request_id(response)
    return stream_response(request_id, response, output_stream)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def download_logs(cluster_name: str,
                  job_ids: Optional[List[str]]) -> Dict[str, str]:
    """Downloads the logs of jobs.

    Args:
        cluster_name: (str) name of the cluster.
        job_ids: (List[str]) job ids.

    Returns:
        The request ID of the download_logs request.

    Request Returns:
        job_log_paths (Dict[str, str]): a mapping of job_id to local log path.

    Request Raises:
        sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
        sky.exceptions.ClusterNotUpError: if the cluster is not UP.
        sky.exceptions.NotSupportedError: if the cluster is not based on
          CloudVmRayBackend.
        sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
          not the same as the user who created the cluster.
        sky.exceptions.CloudUserIdentityError: if we fail to get the current
          user identity.
    """
    body = payloads.ClusterJobsDownloadLogsBody(
        cluster_name=cluster_name,
        job_ids=job_ids,
    )
    response = requests.post(f'{server_common.get_server_url()}/download_logs',
                             json=json.loads(body.model_dump_json()),
                             cookies=server_common.get_api_cookie_jar())
    job_id_remote_path_dict = stream_and_get(
        server_common.get_request_id(response))
    remote2local_path_dict = client_common.download_logs_from_api_server(
        job_id_remote_path_dict.values())
    return {
        job_id: remote2local_path_dict[remote_path]
        for job_id, remote_path in job_id_remote_path_dict.items()
    }


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
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
            flag is equivalent to running ``sky.launch()`` and then
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
        The request ID of the start request.

    Request Returns:
        None

    Request Raises:
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
        cookies=server_common.get_api_cookie_jar(),
    )
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
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
        The request ID of the down request.

    Request Returns:
        None

    Request Raises:
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
        cookies=server_common.get_api_cookie_jar(),
    )
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
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
        The request ID of the stop request.

    Request Returns:
        None

    Request Raises:
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
        cookies=server_common.get_api_cookie_jar(),
    )
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
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
        The request ID of the autostop request.

    Request Returns:
        None

    Request Raises:
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
        cookies=server_common.get_api_cookie_jar(),
    )
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def queue(cluster_name: str,
          skip_finished: bool = False,
          all_users: bool = False) -> server_common.RequestId:
    """Gets the job queue of a cluster.

    Args:
        cluster_name: name of the cluster.
        skip_finished: if True, skip finished jobs.
        all_users: if True, return jobs from all users.


    Returns:
        The request ID of the queue request.

    Request Returns:
        job_records (List[Dict[str, Any]]): A list of dicts for each job in the
            queue.

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

    Request Raises:
        sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
        sky.exceptions.ClusterNotUpError: if the cluster is not UP.
        sky.exceptions.NotSupportedError: if the cluster is not based on
            ``CloudVmRayBackend``.
        sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
            not the same as the user who created the cluster.
        sky.exceptions.CloudUserIdentityError: if we fail to get the current
            user identity.
        sky.exceptions.CommandError: if failed to get the job queue with ssh.
    """
    body = payloads.QueueBody(
        cluster_name=cluster_name,
        skip_finished=skip_finished,
        all_users=all_users,
    )
    response = requests.post(f'{server_common.get_server_url()}/queue',
                             json=json.loads(body.model_dump_json()),
                             cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def job_status(cluster_name: str,
               job_ids: Optional[List[int]] = None) -> server_common.RequestId:
    """Gets the status of jobs on a cluster.

    Args:
        cluster_name: name of the cluster.
        job_ids: job ids. If None, get the status of the last job.

    Returns:
        The request ID of the job status request.

    Request Returns:
        job_statuses (Dict[Optional[int], Optional[job_lib.JobStatus]]): A
            mapping of job_id to job statuses. The status will be None if the
            job does not exist. If job_ids is None and there is no job on the
            cluster, it will return {None: None}.

    Request Raises:
        sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
        sky.exceptions.ClusterNotUpError: if the cluster is not UP.
        sky.exceptions.NotSupportedError: if the cluster is not based on
            ``CloudVmRayBackend``.
        sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
            not the same as the user who created the cluster.
        sky.exceptions.CloudUserIdentityError: if we fail to get the current
            user identity.
    """
    # TODO: merge this into the queue endpoint, i.e., let the queue endpoint
    # take job_ids to filter the returned jobs.
    body = payloads.JobStatusBody(
        cluster_name=cluster_name,
        job_ids=job_ids,
    )
    response = requests.post(f'{server_common.get_server_url()}/job_status',
                             json=json.loads(body.model_dump_json()),
                             cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
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
        The request ID of the cancel request.

    Request Returns:
        None

    Request Raises:
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
                             json=json.loads(body.model_dump_json()),
                             cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def status(
    cluster_names: Optional[List[str]] = None,
    refresh: common.StatusRefreshMode = common.StatusRefreshMode.NONE,
    all_users: bool = False,
) -> server_common.RequestId:
    """Gets cluster statuses.

    If cluster_names is given, return those clusters. Otherwise, return all
    clusters.

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

    Returns:
        The request ID of the status request.

    Request Returns:
        cluster_records (List[Dict[str, Any]]): A list of dicts, with each dict
          containing the information of a cluster. If a cluster is found to be
          terminated or not found, it will be omitted from the returned list.

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
              'resources_str': (str) the resource string representation of the
                cluster,
            }

    """
    # TODO(zhwu): this does not stream the logs output by logger back to the
    # user, due to the rich progress implementation.
    body = payloads.StatusBody(
        cluster_names=cluster_names,
        refresh=refresh,
        all_users=all_users,
    )
    response = requests.post(f'{server_common.get_server_url()}/status',
                             json=json.loads(body.model_dump_json()),
                             cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def endpoints(
        cluster: str,
        port: Optional[Union[int, str]] = None) -> server_common.RequestId:
    """Gets the endpoint for a given cluster and port number (endpoint).

    Args:
        cluster: The name of the cluster.
        port: The port number to get the endpoint for. If None, endpoints
            for all ports are returned.

    Returns:
        The request ID of the endpoints request.

    Request Returns:
        A dictionary of port numbers to endpoints. If port is None,
        the dictionary will contain all ports:endpoints exposed on the cluster.

    Request Raises:
        ValueError: if the cluster is not UP or the endpoint is not exposed.
        RuntimeError: if the cluster has no ports to be exposed or no endpoints
            are exposed yet.
    """
    body = payloads.EndpointsBody(
        cluster=cluster,
        port=port,
    )
    response = requests.post(f'{server_common.get_server_url()}/endpoints',
                             json=json.loads(body.model_dump_json()),
                             cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def cost_report() -> server_common.RequestId:  # pylint: disable=redefined-builtin
    """Gets all cluster cost reports, including those that have been downed.

    The estimated cost column indicates price for the cluster based on the type
    of resources being used and the duration of use up until the call to
    status. This means if the cluster is UP, successive calls to report will
    show increasing price. The estimated cost is calculated based on the local
    cache of the cluster status, and may not be accurate for the cluster with
    autostop/use_spot set or terminated/stopped on the cloud console.

    Returns:
        The request ID of the cost report request.

    Request Returns:
        cluster_cost_records (List[Dict[str, Any]]): A list of dicts, with each
          dict containing the cost information of a cluster.

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
    """
    response = requests.get(f'{server_common.get_server_url()}/cost_report',
                            cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


# === Storage APIs ===
@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def storage_ls() -> server_common.RequestId:
    """Gets the storages.

    Returns:
        The request ID of the storage list request.

    Request Returns:
        storage_records (List[Dict[str, Any]]): A list of dicts, with each dict
            containing the information of a storage.

            .. code-block:: python

                {
                    'name': (str) storage name,
                    'launched_at': (int) timestamp of creation,
                    'store': (List[sky.StoreType]) storage type,
                    'last_use': (int) timestamp of last use,
                    'status': (sky.StorageStatus) storage status,
                }
        ]
    """
    response = requests.get(f'{server_common.get_server_url()}/storage/ls',
                            cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def storage_delete(name: str) -> server_common.RequestId:
    """Deletes a storage.

    Args:
        name: The name of the storage to delete.

    Returns:
        The request ID of the storage delete request.

    Request Returns:
        None

    Request Raises:
        ValueError: If the storage does not exist.
    """
    body = payloads.StorageBody(name=name)
    response = requests.post(f'{server_common.get_server_url()}/storage/delete',
                             json=json.loads(body.model_dump_json()),
                             cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


# === Kubernetes ===


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def local_up(gpus: bool,
             ips: Optional[List[str]],
             ssh_user: Optional[str],
             ssh_key: Optional[str],
             cleanup: bool,
             context_name: Optional[str] = None,
             password: Optional[str] = None) -> server_common.RequestId:
    """Launches a Kubernetes cluster on local machines.

    Returns:
        request_id: The request ID of the local up request.
    """
    # We do not allow local up when the API server is running remotely since it
    # will modify the kubeconfig.
    # TODO: move this check to server.
    if not server_common.is_api_server_local():
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                'sky local up is only supported when running SkyPilot locally.')

    body = payloads.LocalUpBody(gpus=gpus,
                                ips=ips,
                                ssh_user=ssh_user,
                                ssh_key=ssh_key,
                                cleanup=cleanup,
                                context_name=context_name,
                                password=password)
    response = requests.post(f'{server_common.get_server_url()}/local_up',
                             json=json.loads(body.model_dump_json()),
                             cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def local_down() -> server_common.RequestId:
    """Tears down the Kubernetes cluster started by local_up."""
    # We do not allow local up when the API server is running remotely since it
    # will modify the kubeconfig.
    # TODO: move this check to remote server.
    if not server_common.is_api_server_local():
        with ux_utils.print_exception_no_traceback():
            raise ValueError('sky local down is only supported when running '
                             'SkyPilot locally.')
    response = requests.post(f'{server_common.get_server_url()}/local_down',
                             cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def realtime_kubernetes_gpu_availability(
        context: Optional[str] = None,
        name_filter: Optional[str] = None,
        quantity_filter: Optional[int] = None) -> server_common.RequestId:
    """Gets the real-time Kubernetes GPU availability.

    Returns:
        The request ID of the real-time Kubernetes GPU availability request.
    """
    body = payloads.RealtimeGpuAvailabilityRequestBody(
        context=context,
        name_filter=name_filter,
        quantity_filter=quantity_filter,
    )
    response = requests.post(
        f'{server_common.get_server_url()}/'
        'realtime_kubernetes_gpu_availability',
        json=json.loads(body.model_dump_json()),
        cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
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
        json=json.loads(body.model_dump_json()),
        cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
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
        f'{server_common.get_server_url()}/status_kubernetes',
        cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


# === API request APIs ===
@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
def get(request_id: str) -> Any:
    """Waits for and gets the result of a request.

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
    response = requests.get(
        f'{server_common.get_server_url()}/api/get?request_id={request_id}',
        timeout=(client_common.API_SERVER_REQUEST_CONNECTION_TIMEOUT_SECONDS,
                 None),
        cookies=server_common.get_api_cookie_jar())
    request_task = None
    if response.status_code == 200:
        request_task = requests_lib.Request.decode(
            requests_lib.RequestPayload(**response.json()))
    elif response.status_code == 500:
        try:
            request_task = requests_lib.Request.decode(
                requests_lib.RequestPayload(**response.json().get('detail')))
            logger.debug(f'Got request with error: {request_task.name}')
        except Exception:  # pylint: disable=broad-except
            request_task = None
    if request_task is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(f'Failed to get request {request_id}: '
                               f'{response.status_code} {response.text}')
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
@annotations.client_api
def stream_and_get(
    request_id: Optional[str] = None,
    log_path: Optional[str] = None,
    tail: Optional[int] = None,
    follow: bool = True,
    output_stream: Optional['io.TextIOBase'] = None,
) -> Any:
    """Streams the logs of a request or a log file and gets the final result.

    This will block until the request is finished. The request id can be a
    prefix of the full request id.

    Args:
        request_id: The prefix of the request ID of the request to stream.
        log_path: The path to the log file to stream.
        tail: The number of lines to show from the end of the logs.
            If None, show all logs.
        follow: Whether to follow the logs.
        output_stream: The output stream to write to. If None, print to the
            console.

    Returns:
        The ``Request Returns`` of the specified request. See the documentation
        of the specific requests above for more details.

    Raises:
        Exception: It raises the same exceptions as the specific requests,
            see ``Request Raises`` in the documentation of the specific requests
            above.
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
        timeout=(client_common.API_SERVER_REQUEST_CONNECTION_TIMEOUT_SECONDS,
                 None),
        stream=True,
        cookies=server_common.get_api_cookie_jar())
    if response.status_code in [404, 400]:
        detail = response.json().get('detail')
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(f'Failed to stream logs: {detail}')
    elif response.status_code != 200:
        return get(request_id)
    return stream_response(request_id, response, output_stream)


@usage_lib.entrypoint
@annotations.client_api
def api_cancel(request_ids: Optional[Union[str, List[str]]] = None,
               all_users: bool = False,
               silent: bool = False) -> server_common.RequestId:
    """Aborts a request or all requests.

    Args:
        request_ids: The request ID(s) to abort. Can be a single string or a
            list of strings.
        all_users: Whether to abort all requests from all users.
        silent: Whether to suppress the output.

    Returns:
        The request ID of the abort request itself.

    Request Returns:
        A list of request IDs that were cancelled.

    Raises:
        click.BadParameter: If no request ID is specified and not all or
            all_users is not set.
    """
    echo = logger.info if not silent else logger.debug
    user_id = None
    if not all_users:
        user_id = common_utils.get_user_hash()

    # Convert single request ID to list if needed
    if isinstance(request_ids, str):
        request_ids = [request_ids]

    body = payloads.RequestCancelBody(request_ids=request_ids, user_id=user_id)
    if all_users:
        echo('Cancelling all users\' requests...')
    elif request_ids is None:
        echo(f'Cancelling all requests for user {user_id!r}...')
    else:
        request_id_str = ', '.join(
            repr(request_id) for request_id in request_ids)
        plural = 's' if len(request_ids) > 1 else ''
        echo(f'Cancelling {len(request_ids)} request{plural}: '
             f'{request_id_str}...')

    response = requests.post(f'{server_common.get_server_url()}/api/cancel',
                             json=json.loads(body.model_dump_json()),
                             timeout=5,
                             cookies=server_common.get_api_cookie_jar())
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@annotations.client_api
def api_status(
    request_ids: Optional[List[str]] = None,
    # pylint: disable=redefined-builtin
    all_status: bool = False
) -> List[requests_lib.RequestPayload]:
    """Lists all requests.

    Args:
        request_ids: The prefixes of the request IDs of the requests to query.
            If None, all requests are queried.
        all_status: Whether to list all finished requests as well. This argument
            is ignored if request_ids is not None.

    Returns:
        A list of request payloads.
    """
    body = payloads.RequestStatusBody(request_ids=request_ids,
                                      all_status=all_status)
    response = requests.get(
        f'{server_common.get_server_url()}/api/status',
        params=server_common.request_body_to_params(body),
        timeout=(client_common.API_SERVER_REQUEST_CONNECTION_TIMEOUT_SECONDS,
                 None),
        cookies=server_common.get_api_cookie_jar())
    server_common.handle_request_error(response)
    return [
        requests_lib.RequestPayload(**request) for request in response.json()
    ]


# === API server management APIs ===
@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@annotations.client_api
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
    response = requests.get(f'{server_common.get_server_url()}/api/health',
                            cookies=server_common.get_api_cookie_jar())
    response.raise_for_status()
    return response.json()


@usage_lib.entrypoint
@annotations.client_api
def api_start(
    *,
    deploy: bool = False,
    host: str = '127.0.0.1',
    foreground: bool = False,
) -> None:
    """Starts the API server.

    It checks the existence of the API server and starts it if it does not
    exist.

    Args:
        deploy: Whether to deploy the API server, i.e. fully utilize the
            resources of the machine.
        host: The host to deploy the API server. It will be set to 0.0.0.0
            if deploy is True, to allow remote access.
        foreground: Whether to run the API server in the foreground (run in
            the current process).
    Returns:
        None
    """
    if deploy:
        host = '0.0.0.0'
    if host not in server_common.AVAILBLE_LOCAL_API_SERVER_HOSTS:
        raise ValueError(f'Invalid host: {host}. Should be one of: '
                         f'{server_common.AVAILBLE_LOCAL_API_SERVER_HOSTS}')
    is_local_api_server = server_common.is_api_server_local()
    if not is_local_api_server:
        server_url = server_common.get_server_url()
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Unable to start local API server: '
                             f'server endpoint is set to {server_url}. '
                             'To start a local API server, remove the endpoint '
                             'from the config file and/or unset the '
                             'SKYPILOT_API_SERVER_ENDPOINT environment '
                             'variable.')
    server_common.check_server_healthy_or_start_fn(deploy, host, foreground)
    if foreground:
        # Explain why current process exited
        logger.info('API server is already running:')
    logger.info(f'{ux_utils.INDENT_SYMBOL}SkyPilot API server: '
                f'{server_common.get_server_url(host)}\n'
                f'{ux_utils.INDENT_LAST_SYMBOL}'
                f'View API server logs at: {constants.API_SERVER_LOGS}')


@usage_lib.entrypoint
@annotations.client_api
def api_stop() -> None:
    """Stops the API server.

    It will do nothing if the API server is remotely hosted.

    Returns:
        None
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

    # Remove the database for requests.
    server_common.clear_local_api_server_database()

    if found:
        logger.info(f'{colorama.Fore.GREEN}SkyPilot API server stopped.'
                    f'{colorama.Style.RESET_ALL}')
    else:
        logger.info('SkyPilot API server is not running.')


# Use the same args as `docker logs`
@usage_lib.entrypoint
@annotations.client_api
def api_server_logs(follow: bool = True, tail: Optional[int] = None) -> None:
    """Streams the API server logs.

    Args:
        follow: Whether to follow the logs.
        tail: the number of lines to show from the end of the logs.
            If None, show all logs.

    Returns:
        None
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
@annotations.client_api
def api_login(endpoint: Optional[str] = None) -> None:
    """Logs into a SkyPilot API server.

    This sets the endpoint globally, i.e., all SkyPilot CLI and SDK calls will
    use this endpoint.

    To temporarily override the endpoint, use the environment variable
    `SKYPILOT_API_SERVER_ENDPOINT` instead.

    Args:
        endpoint: The endpoint of the SkyPilot API server, e.g.,
            http://1.2.3.4:46580 or https://skypilot.mydomain.com.

    Returns:
        None
    """
    # TODO(zhwu): this SDK sets global endpoint, which may not be the best
    # design as a user may expect this is only effective for the current
    # session. We should consider using env var for specifying endpoint.
    if endpoint is None:
        endpoint = click.prompt('Enter your SkyPilot API server endpoint')
    # Check endpoint is a valid URL
    if (endpoint is not None and not endpoint.startswith('http://') and
            not endpoint.startswith('https://')):
        raise click.BadParameter('Endpoint must be a valid URL.')

    server_common.check_server_healthy(endpoint)

    # Set the endpoint in the config file
    config_path = pathlib.Path(
        skypilot_config.get_user_config_path()).expanduser()
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
