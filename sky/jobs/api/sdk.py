"""SDK functions for managed jobs."""
import json
import tempfile
import typing
from typing import List, Optional, Union

import click
import requests

from sky.api import common as api_common
from sky.api.requests import payloads
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import dag_utils

if typing.TYPE_CHECKING:
    import sky


@usage_lib.entrypoint
@api_common.check_health
def launch(
    task: Union['sky.Task', 'sky.Dag'],
    name: Optional[str] = None,
    detach_run: bool = False,
    retry_until_up: bool = False,
    need_confirmation: bool = False,
) -> str:
    """Launch a managed job."""
    from sky.api import sdk  # pylint: disable=import-outside-toplevel

    dag = api_common.upload_mounts_to_api_server(task)
    with tempfile.NamedTemporaryFile(mode='r') as f:
        dag_utils.dump_chain_dag_to_yaml(dag, f.name)
        dag_str = f.read()

    request_id = sdk.optimize(dag)
    sdk.stream_and_get(request_id)
    if need_confirmation:
        prompt = f'Launching a managed job {dag.name!r}. Proceed?'
        if prompt is not None:
            click.confirm(prompt, default=True, abort=True, show_default=True)

    body = payloads.JobsLaunchBody(
        task=dag_str,
        name=name,
        detach_run=detach_run,
        retry_until_up=retry_until_up,
    )
    response = requests.post(
        f'{api_common.get_server_url()}/jobs/launch',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
    )
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def queue(refresh: bool, skip_finished: bool = False) -> str:
    """Get statuses of managed jobs."""
    body = payloads.JobsQueueBody(
        refresh=refresh,
        skip_finished=skip_finished,
    )
    response = requests.get(
        f'{api_common.get_server_url()}/jobs/queue',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
    )
    return api_common.get_request_id(response=response)


@usage_lib.entrypoint
@api_common.check_health
def cancel(
        name: Optional[str] = None,
        job_ids: Optional[List[int]] = None,
        all: bool = False,  # pylint: disable=redefined-builtin
) -> str:
    """Cancel managed jobs."""
    body = payloads.JobsCancelBody(
        name=name,
        job_ids=job_ids,
        all=all,
    )
    response = requests.post(
        f'{api_common.get_server_url()}/jobs/cancel',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
    )
    return api_common.get_request_id(response=response)


@usage_lib.entrypoint
@api_common.check_health
def tail_logs(name: Optional[str], job_id: Optional[int], follow: bool,
              controller: bool) -> str:
    """Tail logs of managed jobs."""
    body = payloads.JobsLogsBody(
        name=name,
        job_id=job_id,
        follow=follow,
        controller=controller,
    )
    response = requests.get(
        f'{api_common.get_server_url()}/jobs/logs',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
    )
    return api_common.get_request_id(response=response)


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
