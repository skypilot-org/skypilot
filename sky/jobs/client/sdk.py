"""SDK functions for managed jobs."""
import json
import typing
from typing import Dict, List, Optional, Union
import webbrowser

import click

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.client import common as client_common
from sky.client import sdk
from sky.server import common as server_common
from sky.server.requests import payloads
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import dag_utils

if typing.TYPE_CHECKING:
    import io

    import requests

    import sky
else:
    requests = adaptors_common.LazyImport('requests')

logger = sky_logging.init_logger(__name__)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def launch(
    task: Union['sky.Task', 'sky.Dag'],
    name: Optional[str] = None,
    # Internal only:
    # pylint: disable=invalid-name
    _need_confirmation: bool = False,
) -> server_common.RequestId:
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

    dag = dag_utils.convert_entrypoint_to_dag(task)
    sdk.validate(dag)
    if _need_confirmation:
        request_id = sdk.optimize(dag)
        sdk.stream_and_get(request_id)
        prompt = f'Launching a managed job {dag.name!r}. Proceed?'
        if prompt is not None:
            click.confirm(prompt, default=True, abort=True, show_default=True)

    dag = client_common.upload_mounts_to_api_server(dag)
    dag_str = dag_utils.dump_chain_dag_to_yaml_str(dag)
    body = payloads.JobsLaunchBody(
        task=dag_str,
        name=name,
    )
    response = requests.post(
        f'{server_common.get_server_url()}/jobs/launch',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
        cookies=server_common.get_api_cookie_jar(),
    )
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def queue(refresh: bool,
          skip_finished: bool = False,
          all_users: bool = False) -> server_common.RequestId:
    """Gets statuses of managed jobs.

    Please refer to sky.cli.job_queue for documentation.

    Args:
        refresh: Whether to restart the jobs controller if it is stopped.
        skip_finished: Whether to skip finished jobs.
        all_users: Whether to show all users' jobs.

    Returns:
        The request ID of the queue request.

    Request Returns:
        job_records (List[Dict[str, Any]]): A list of dicts, with each dict
          containing the information of a job.

          .. code-block:: python

            [
              {
                'job_id': (int) job id,
                'job_name': (str) job name,
                'resources': (str) resources of the job,
                'submitted_at': (float) timestamp of submission,
                'end_at': (float) timestamp of end,
                'duration': (float) duration in seconds,
                'recovery_count': (int) Number of retries,
                'status': (sky.jobs.ManagedJobStatus) of the job,
                'cluster_resources': (str) resources of the cluster,
                'region': (str) region of the cluster,
              }
            ]

    Request Raises:
        sky.exceptions.ClusterNotUpError: the jobs controller is not up or
          does not exist.
        RuntimeError: if failed to get the managed jobs with ssh.
    """
    body = payloads.JobsQueueBody(
        refresh=refresh,
        skip_finished=skip_finished,
        all_users=all_users,
    )
    response = requests.post(
        f'{server_common.get_server_url()}/jobs/queue',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
        cookies=server_common.get_api_cookie_jar(),
    )
    return server_common.get_request_id(response=response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def cancel(
    name: Optional[str] = None,
    job_ids: Optional[List[int]] = None,
    all: bool = False,  # pylint: disable=redefined-builtin
    all_users: bool = False,
) -> server_common.RequestId:
    """Cancels managed jobs.

    Please refer to sky.cli.job_cancel for documentation.

    Args:
        name: Name of the managed job to cancel.
        job_ids: IDs of the managed jobs to cancel.
        all: Whether to cancel all managed jobs.
        all_users: Whether to cancel all managed jobs from all users.

    Returns:
        The request ID of the cancel request.

    Request Raises:
        sky.exceptions.ClusterNotUpError: the jobs controller is not up.
        RuntimeError: failed to cancel the job.
    """
    body = payloads.JobsCancelBody(
        name=name,
        job_ids=job_ids,
        all=all,
        all_users=all_users,
    )
    response = requests.post(
        f'{server_common.get_server_url()}/jobs/cancel',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
        cookies=server_common.get_api_cookie_jar(),
    )
    return server_common.get_request_id(response=response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def tail_logs(name: Optional[str] = None,
              job_id: Optional[int] = None,
              follow: bool = True,
              controller: bool = False,
              refresh: bool = False,
              output_stream: Optional['io.TextIOBase'] = None) -> int:
    """Tails logs of managed jobs.

    You can provide either a job name or a job ID to tail logs. If both are not
    provided, the logs of the latest job will be shown.

    Args:
        name: Name of the managed job to tail logs.
        job_id: ID of the managed job to tail logs.
        follow: Whether to follow the logs.
        controller: Whether to tail logs from the jobs controller.
        refresh: Whether to restart the jobs controller if it is stopped.
        output_stream: The stream to write the logs to. If None, print to the
            console.

    Returns:
        Exit code based on success or failure of the job. 0 if success,
        100 if the job failed. See exceptions.JobExitCode for possible exit
        codes.

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
    )
    response = requests.post(
        f'{server_common.get_server_url()}/jobs/logs',
        json=json.loads(body.model_dump_json()),
        stream=True,
        timeout=(5, None),
        cookies=server_common.get_api_cookie_jar(),
    )
    request_id = server_common.get_request_id(response)
    return sdk.stream_response(request_id, response, output_stream)


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
    response = requests.post(
        f'{server_common.get_server_url()}/jobs/download_logs',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
        cookies=server_common.get_api_cookie_jar(),
    )
    job_id_remote_path_dict = sdk.stream_and_get(
        server_common.get_request_id(response))
    remote2local_path_dict = client_common.download_logs_from_api_server(
        job_id_remote_path_dict.values())
    return {
        job_id: remote2local_path_dict[remote_path]
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
