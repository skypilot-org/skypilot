"""SDK functions for managed jobs."""
import json
import typing
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import click

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.client import common as client_common
from sky.client import sdk
from sky.jobs import utils as jobs_utils
from sky.schemas.api import responses
from sky.serve.client import impl
from sky.server import common as server_common
from sky.server import rest
from sky.server import versions
from sky.server.requests import payloads
from sky.server.requests import request_names
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import admin_policy_utils
from sky.utils import common_utils
from sky.utils import context
from sky.utils import dag_utils

if typing.TYPE_CHECKING:
    import io
    import webbrowser

    import sky
    from sky import backends
    from sky.serve import serve_utils
else:
    # only used in dashboard()
    webbrowser = adaptors_common.LazyImport('webbrowser')

logger = sky_logging.init_logger(__name__)


@context.contextual
@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def launch(
    task: Union['sky.Task', 'sky.Dag'],
    name: Optional[str] = None,
    pool: Optional[str] = None,
    num_jobs: Optional[int] = None,
    # Internal only:
    # pylint: disable=invalid-name
    _need_confirmation: bool = False,
) -> server_common.RequestId[Tuple[Optional[int],
                                   Optional['backends.ResourceHandle']]]:
    """Launches a managed job.

    Please refer to sky.cli.job_launch for documentation.

    Args:
        task: sky.Task, or sky.Dag (experimental; 1-task only) to launch as a
            managed job.
        name: Name of the managed job.
        _need_confirmation: (Internal only) Whether to show a confirmation
            prompt before launching the job.

    Returns:
        The request ID of the launch request.

    Request Returns:
        job_id (Optional[int]): Job ID for the managed job
        controller_handle (Optional[ResourceHandle]): ResourceHandle of the
          controller

    Request Raises:
        ValueError: cluster does not exist. Or, the entrypoint is not a valid
          chain dag.
        sky.exceptions.NotSupportedError: the feature is not supported.
    """
    remote_api_version = versions.get_remote_api_version()
    if (pool is not None and
        (remote_api_version is None or remote_api_version < 12)):
        raise click.UsageError('Pools are not supported in your API server. '
                               'Please upgrade to a newer API server to use '
                               'pools.')
    if pool is None and num_jobs is not None:
        raise click.UsageError('Cannot specify num_jobs without pool.')

    dag = dag_utils.convert_entrypoint_to_dag(task)
    if pool is not None:
        jobs_utils.validate_pool_job(dag, pool)

    with admin_policy_utils.apply_and_use_config_in_current_request(
            dag,
            request_name=request_names.AdminPolicyRequestName.JOBS_LAUNCH,
            at_client_side=True) as dag:
        sdk.validate(dag)
        if _need_confirmation:
            job_identity = 'a managed job'
            if pool is None:
                optimize_request_id = sdk.optimize(dag)
                sdk.stream_and_get(optimize_request_id)
            else:
                pool_status_request_id = pool_status(pool)
                pool_statuses = sdk.get(pool_status_request_id)
                if not pool_statuses:
                    raise click.UsageError(f'Pool {pool!r} not found.')
                resources = pool_statuses[0]['requested_resources_str']
                click.secho(f'Use resources from pool {pool!r}: {resources}.',
                            fg='green')
                if num_jobs is not None:
                    job_identity = f'{num_jobs} managed jobs'
            prompt = f'Launching {job_identity} {dag.name!r}. Proceed?'
            if prompt is not None:
                click.confirm(prompt,
                              default=True,
                              abort=True,
                              show_default=True)

        dag = client_common.upload_mounts_to_api_server(dag)
        dag_str = dag_utils.dump_chain_dag_to_yaml_str(dag)
        body = payloads.JobsLaunchBody(
            task=dag_str,
            name=name,
            pool=pool,
            num_jobs=num_jobs,
        )
        response = server_common.make_authenticated_request(
            'POST',
            '/jobs/launch',
            json=json.loads(body.model_dump_json()),
            timeout=(5, None))
        return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def queue(
    refresh: bool,
    skip_finished: bool = False,
    all_users: bool = False,
    job_ids: Optional[List[int]] = None,
    limit: Optional[int] = None,
    fields: Optional[List[str]] = None,
) -> server_common.RequestId[Union[List[responses.ManagedJobRecord], Tuple[
        List[responses.ManagedJobRecord], int, Dict[str, int], int]]]:
    """Gets statuses of managed jobs.

    Please refer to sky.cli.job_queue for documentation.

    Args:
        refresh: Whether to restart the jobs controller if it is stopped.
        skip_finished: Whether to skip finished jobs.
        all_users: Whether to show all users' jobs.
        job_ids: IDs of the managed jobs to show.
        limit: Number of jobs to show.
        fields: Fields to get for the managed jobs.

    Returns:
        The request ID of the queue request.

    Request Returns:
        job_records (List[responses.ManagedJobRecord]): A list of dicts, with each dict
          containing the information of a job.

          .. code-block:: python

            [
              {
                'job_id': (int) job id,
                'job_name': (str) job name,
                'resources': (str) resources of the job,
                'submitted_at': (float) timestamp of submission,
                'end_at': (float) timestamp of end,
                'job_duration': (float) duration in seconds,
                'recovery_count': (int) Number of retries,
                'status': (sky.jobs.ManagedJobStatus) of the job,
                'cluster_resources': (str) resources of the cluster,
                'region': (str) region of the cluster,
                'task_id': (int), set to 0 (except in pipelines, which may have multiple tasks), # pylint: disable=line-too-long
                'task_name': (str), same as job_name (except in pipelines, which may have multiple tasks), # pylint: disable=line-too-long
              }
            ]

    Request Raises:
        sky.exceptions.ClusterNotUpError: the jobs controller is not up or
          does not exist.
        RuntimeError: if failed to get the managed jobs with ssh.
    """
    remote_api_version = versions.get_remote_api_version()
    if remote_api_version and remote_api_version >= 18:
        body = payloads.JobsQueueV2Body(
            refresh=refresh,
            skip_finished=skip_finished,
            all_users=all_users,
            job_ids=job_ids,
            limit=limit,
            fields=fields,
        )
        path = '/jobs/queue/v2'
    else:
        body = payloads.JobsQueueBody(
            refresh=refresh,
            skip_finished=skip_finished,
            all_users=all_users,
            job_ids=job_ids,
        )
        path = '/jobs/queue'

    response = server_common.make_authenticated_request(
        'POST',
        path,
        json=json.loads(body.model_dump_json()),
        timeout=(5, None))
    return server_common.get_request_id(response=response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def cancel(
    name: Optional[str] = None,
    job_ids: Optional[Sequence[int]] = None,
    all: bool = False,  # pylint: disable=redefined-builtin
    all_users: bool = False,
    pool: Optional[str] = None,
) -> server_common.RequestId[None]:
    """Cancels managed jobs.

    Please refer to sky.cli.job_cancel for documentation.

    Args:
        name: Name of the managed job to cancel.
        job_ids: IDs of the managed jobs to cancel.
        all: Whether to cancel all managed jobs.
        all_users: Whether to cancel all managed jobs from all users.
        pool: Pool name to cancel.

    Returns:
        The request ID of the cancel request.

    Request Raises:
        sky.exceptions.ClusterNotUpError: the jobs controller is not up.
        RuntimeError: failed to cancel the job.
    """
    remote_api_version = versions.get_remote_api_version()
    if (pool is not None and
        (remote_api_version is None or remote_api_version < 12)):
        raise click.UsageError('Pools are not supported in your API server. '
                               'Please upgrade to a newer API server to use '
                               'pools.')
    body = payloads.JobsCancelBody(
        name=name,
        job_ids=job_ids,
        all=all,
        all_users=all_users,
        pool=pool,
    )
    response = server_common.make_authenticated_request(
        'POST',
        '/jobs/cancel',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None))
    return server_common.get_request_id(response=response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@rest.retry_transient_errors()
def tail_logs(name: Optional[str] = None,
              job_id: Optional[int] = None,
              follow: bool = True,
              controller: bool = False,
              refresh: bool = False,
              tail: Optional[int] = None,
              output_stream: Optional['io.TextIOBase'] = None) -> Optional[int]:
    """Tails logs of managed jobs.

    You can provide either a job name or a job ID to tail logs. If both are not
    provided, the logs of the latest job will be shown.

    Args:
        name: Name of the managed job to tail logs.
        job_id: ID of the managed job to tail logs.
        follow: Whether to follow the logs.
        controller: Whether to tail logs from the jobs controller.
        refresh: Whether to restart the jobs controller if it is stopped.
        tail: Number of lines to tail from the end of the log file.
        output_stream: The stream to write the logs to. If None, print to the
            console.

    Returns:
        Exit code based on success or failure of the job. 0 if success,
        100 if the job failed. See exceptions.JobExitCode for possible exit
        codes.
        Will return None if follow is False
        (see note in sky/client/sdk.py::stream_response)

    Request Raises:
        ValueError: invalid arguments.
        sky.exceptions.ClusterNotUpError: the jobs controller is not up.
    """
    body = payloads.JobsLogsBody(
        name=name,
        job_id=job_id,
        follow=follow,
        controller=controller,
        refresh=refresh,
        tail=tail,
    )
    response = server_common.make_authenticated_request(
        'POST',
        '/jobs/logs',
        json=json.loads(body.model_dump_json()),
        stream=True,
        timeout=(5, None))
    request_id: server_common.RequestId[int] = server_common.get_request_id(
        response)
    # Log request is idempotent when tail is 0, thus can resume previous
    # streaming point on retry.
    return sdk.stream_response(request_id=request_id,
                               response=response,
                               output_stream=output_stream,
                               resumable=(tail == 0),
                               get_result=follow)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def download_logs(
        name: Optional[str],
        job_id: Optional[int],
        refresh: bool,
        controller: bool,
        local_dir: str = constants.SKY_LOGS_DIRECTORY) -> Dict[int, str]:
    """Sync down logs of managed jobs.

    Please refer to sky.cli.job_logs for documentation.

    Args:
        name: Name of the managed job to sync down logs.
        job_id: ID of the managed job to sync down logs.
        refresh: Whether to restart the jobs controller if it is stopped.
        controller: Whether to sync down logs from the jobs controller.
        local_dir: Local directory to sync down logs.

    Returns:
        A dictionary mapping job ID to the local path.

    Request Raises:
        ValueError: invalid arguments.
        sky.exceptions.ClusterNotUpError: the jobs controller is not up.
    """

    body = payloads.JobsDownloadLogsBody(
        name=name,
        job_id=job_id,
        refresh=refresh,
        controller=controller,
        local_dir=local_dir,
    )
    response = server_common.make_authenticated_request(
        'POST',
        '/jobs/download_logs',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None))
    request_id: server_common.RequestId[Dict[
        str, str]] = server_common.get_request_id(response)
    job_id_remote_path_dict = sdk.stream_and_get(request_id)
    remote2local_path_dict = client_common.download_logs_from_api_server(
        job_id_remote_path_dict.values())
    return {
        int(job_id): remote2local_path_dict[remote_path]
        for job_id, remote_path in job_id_remote_path_dict.items()
    }


spot_launch = common_utils.deprecated_function(
    launch,
    name='sky.jobs.launch',
    deprecated_name='spot_launch',
    removing_version='0.8.0',
    override_argument={'use_spot': True})
spot_queue = common_utils.deprecated_function(queue,
                                              name='sky.jobs.queue',
                                              deprecated_name='spot_queue',
                                              removing_version='0.8.0')
spot_cancel = common_utils.deprecated_function(cancel,
                                               name='sky.jobs.cancel',
                                               deprecated_name='spot_cancel',
                                               removing_version='0.8.0')
spot_tail_logs = common_utils.deprecated_function(
    tail_logs,
    name='sky.jobs.tail_logs',
    deprecated_name='spot_tail_logs',
    removing_version='0.8.0')


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def dashboard() -> None:
    """Starts a dashboard for managed jobs."""
    user_hash = common_utils.get_user_hash()
    api_server_url = server_common.get_server_url()
    params = f'user_hash={user_hash}'
    url = f'{api_server_url}/jobs/dashboard?{params}'
    logger.info(f'Opening dashboard in browser: {url}')
    webbrowser.open(url)


@context.contextual
@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@versions.minimal_api_version(12)
def pool_apply(
    task: Optional[Union['sky.Task', 'sky.Dag']],
    pool_name: str,
    mode: 'serve_utils.UpdateMode',
    workers: Optional[int] = None,
    # Internal only:
    # pylint: disable=invalid-name
    _need_confirmation: bool = False
) -> server_common.RequestId[None]:
    """Apply a config to a pool."""
    remote_api_version = versions.get_remote_api_version()
    if (workers is not None and
        (remote_api_version is None or remote_api_version < 19)):
        raise click.UsageError('Updating the number of workers in a pool is '
                               'not supported in your API server. Please '
                               'upgrade to a newer API server to use this '
                               'feature.')
    return impl.apply(task,
                      workers,
                      pool_name,
                      mode,
                      pool=True,
                      _need_confirmation=_need_confirmation)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@versions.minimal_api_version(12)
def pool_down(
    pool_names: Optional[Union[str, List[str]]],
    all: bool = False,  # pylint: disable=redefined-builtin
    purge: bool = False,
) -> server_common.RequestId[None]:
    """Delete a pool."""
    return impl.down(pool_names, all, purge, pool=True)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@versions.minimal_api_version(12)
def pool_status(
    pool_names: Optional[Union[str, List[str]]],
) -> server_common.RequestId[List[Dict[str, Any]]]:
    """Query a pool."""
    return impl.status(pool_names, pool=True)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@rest.retry_transient_errors()
@versions.minimal_api_version(16)
def pool_tail_logs(pool_name: str,
                   target: Union[str, 'serve_utils.ServiceComponent'],
                   worker_id: Optional[int] = None,
                   follow: bool = True,
                   output_stream: Optional['io.TextIOBase'] = None,
                   tail: Optional[int] = None) -> None:
    """Tails logs of a pool."""
    return impl.tail_logs(pool_name,
                          target,
                          worker_id,
                          follow,
                          output_stream,
                          tail,
                          pool=True)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@rest.retry_transient_errors()
@versions.minimal_api_version(16)
def pool_sync_down_logs(pool_name: str,
                        local_dir: str,
                        *,
                        targets: Optional[Union[
                            str, 'serve_utils.ServiceComponent', Sequence[Union[
                                str, 'serve_utils.ServiceComponent']]]] = None,
                        worker_ids: Optional[List[int]] = None,
                        tail: Optional[int] = None) -> None:
    """Sync down logs of a pool."""
    return impl.sync_down_logs(pool_name,
                               local_dir,
                               targets=targets,
                               replica_ids=worker_ids,
                               tail=tail,
                               pool=True)
