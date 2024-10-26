"""SDK for SkyServe."""
import json
import tempfile
import typing
from typing import List, Optional, Union

import click
import requests

from sky.api import common as api_common
from sky.api.requests import payloads
from sky.usage import usage_lib
from sky.utils import dag_utils

if typing.TYPE_CHECKING:
    import sky
    from sky.serve import serve_utils


@usage_lib.entrypoint
@api_common.check_health
def up(task: Union['sky.Task', 'sky.Dag'],
       service_name: str,
       need_confirmation: bool = False) -> str:
    """Launch a service."""
    # This is to avoid circular import.
    from sky.api import sdk  # pylint: disable=import-outside-toplevel
    dag = api_common.upload_mounts_to_api_server(task)
    with tempfile.NamedTemporaryFile(mode='r') as f:
        dag_utils.dump_chain_dag_to_yaml(dag, f.name)
        dag_str = f.read()

    request_id = sdk.optimize(dag)
    sdk.stream_and_get(request_id)

    if need_confirmation:
        prompt = f'Launching a new service {service_name!r}. Proceed?'
        if prompt is not None:
            click.confirm(prompt, default=True, abort=True, show_default=True)

    body = payloads.ServeUpBody(
        task=dag_str,
        service_name=service_name,
    )
    response = requests.post(
        f'{api_common.get_server_url()}/serve/up',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
    )
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def update(task: Union['sky.Task', 'sky.Dag'],
           service_name: str,
           mode: 'serve_utils.UpdateMode',
           need_confirmation: bool = False) -> str:
    """Update a service."""
    # This is to avoid circular import.
    from sky.api import sdk  # pylint: disable=import-outside-toplevel
    dag = api_common.upload_mounts_to_api_server(task)
    with tempfile.NamedTemporaryFile(mode='r') as f:
        dag_utils.dump_chain_dag_to_yaml(dag, f.name)
        dag_str = f.read()

    request_id = sdk.optimize(dag)
    sdk.stream_and_get(request_id)

    if need_confirmation:
        click.confirm(f'Updating service {service_name!r}. Proceed?',
                      default=True,
                      abort=True,
                      show_default=True)

    body = payloads.ServeUpdateBody(
        task=dag_str,
        service_name=service_name,
        mode=mode,
    )

    response = requests.post(
        f'{api_common.get_server_url()}/serve/update',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
    )
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def down(
        service_names: Optional[Union[str, List[str]]],
        all: bool = False,  # pylint: disable=redefined-builtin
        purge: bool = False) -> str:
    """Down a service."""
    body = payloads.ServeDownBody(
        service_names=service_names,
        all=all,
        purge=purge,
    )
    response = requests.post(
        f'{api_common.get_server_url()}/serve/down',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
    )
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def terminate_replica(service_name: str, replica_id: int, purge: bool) -> str:
    """Terminate a replica."""
    body = payloads.ServeTerminateReplicaBody(
        service_name=service_name,
        replica_id=replica_id,
        purge=purge,
    )
    response = requests.post(
        f'{api_common.get_server_url()}/serve/terminate-replica',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
    )
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def status(service_names: Optional[Union[str, List[str]]]) -> str:
    """Get the status of a service."""
    body = payloads.ServeStatusBody(service_names=service_names,)
    response = requests.get(
        f'{api_common.get_server_url()}/serve/status',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
    )
    return api_common.get_request_id(response)


@usage_lib.entrypoint
@api_common.check_health
def tail_logs(service_name: str,
              target: Union[str, 'serve_utils.ServiceComponent'],
              replica_id: Optional[int] = None,
              follow: bool = True) -> str:
    """Tail logs of a service."""
    body = payloads.ServeLogsBody(
        service_name=service_name,
        target=target,
        replica_id=replica_id,
        follow=follow,
    )
    response = requests.get(
        f'{api_common.get_server_url()}/serve/logs',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
    )
    return api_common.get_request_id(response)
