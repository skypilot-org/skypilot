"""SDK functions for managed jobs."""
import json
import tempfile
import typing
from typing import List, Optional, Union

import requests

from sky.api import common as api_common
from sky.api.requests import payloads
from sky.usage import usage_lib
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
) -> str:
    """Launch a managed job."""
    dag = api_common.upload_mounts_to_api_server(task)
    with tempfile.NamedTemporaryFile(mode='r') as f:
        dag_utils.dump_chain_dag_to_yaml(dag, f.name)
        dag_str = f.read()

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
