"""Implementation of SDK for SkyServe."""
import json
import typing
from typing import List, Optional, Union

import click

from sky.client import common as client_common
from sky.server import common as server_common
from sky.server.requests import payloads
from sky.utils import admin_policy_utils
from sky.utils import dag_utils

if typing.TYPE_CHECKING:
    import sky
    from sky.serve import serve_utils


def up(
    task: Union['sky.Task', 'sky.Dag'],
    service_name: str,
    pool: bool = False,
    # Internal only:
    # pylint: disable=invalid-name
    _need_confirmation: bool = False
) -> server_common.RequestId:
    assert not pool, 'Command `up` is not supported for pool.'
    # Avoid circular import.
    from sky.client import sdk  # pylint: disable=import-outside-toplevel

    dag = dag_utils.convert_entrypoint_to_dag(task)
    with admin_policy_utils.apply_and_use_config_in_current_request(
            dag, at_client_side=True) as dag:
        sdk.validate(dag)
        request_id = sdk.optimize(dag)
        sdk.stream_and_get(request_id)
        if _need_confirmation:
            noun = 'pool' if pool else 'service'
            prompt = f'Launching a new {noun} {service_name!r}. Proceed?'
            if prompt is not None:
                click.confirm(prompt,
                              default=True,
                              abort=True,
                              show_default=True)

        dag = client_common.upload_mounts_to_api_server(dag)
        dag_str = dag_utils.dump_chain_dag_to_yaml_str(dag)

        body = payloads.ServeUpBody(
            task=dag_str,
            service_name=service_name,
        )

        response = server_common.make_authenticated_request(
            'POST',
            '/serve/up',
            json=json.loads(body.model_dump_json()),
            timeout=(5, None))
        return server_common.get_request_id(response)


def update(
    task: Union['sky.Task', 'sky.Dag'],
    service_name: str,
    mode: 'serve_utils.UpdateMode',
    pool: bool = False,
    # Internal only:
    # pylint: disable=invalid-name
    _need_confirmation: bool = False
) -> server_common.RequestId:
    assert not pool, 'Command `update` is not supported for pool.'
    # Avoid circular import.
    from sky.client import sdk  # pylint: disable=import-outside-toplevel
    noun = 'pool' if pool else 'service'

    dag = dag_utils.convert_entrypoint_to_dag(task)
    with admin_policy_utils.apply_and_use_config_in_current_request(
            dag, at_client_side=True) as dag:
        sdk.validate(dag)
        request_id = sdk.optimize(dag)
        sdk.stream_and_get(request_id)
        if _need_confirmation:
            click.confirm(f'Updating {noun} {service_name!r}. Proceed?',
                          default=True,
                          abort=True,
                          show_default=True)

        dag = client_common.upload_mounts_to_api_server(dag)
        dag_str = dag_utils.dump_chain_dag_to_yaml_str(dag)

        body = payloads.ServeUpdateBody(
            task=dag_str,
            service_name=service_name,
            mode=mode,
        )

        response = server_common.make_authenticated_request(
            'POST',
            '/serve/update',
            json=json.loads(body.model_dump_json()),
            timeout=(5, None))
        return server_common.get_request_id(response)


def apply(
    task: Union['sky.Task', 'sky.Dag'],
    service_name: str,
    mode: 'serve_utils.UpdateMode',
    pool: bool = False,
    # Internal only:
    # pylint: disable=invalid-name
    _need_confirmation: bool = False
) -> server_common.RequestId:
    assert pool, 'Command `apply` is only supported for pool.'
    # Avoid circular import.
    from sky.client import sdk  # pylint: disable=import-outside-toplevel

    dag = dag_utils.convert_entrypoint_to_dag(task)
    with admin_policy_utils.apply_and_use_config_in_current_request(
            dag, at_client_side=True) as dag:
        sdk.validate(dag)
        request_id = sdk.optimize(dag)
        sdk.stream_and_get(request_id)
        if _need_confirmation:
            noun = 'pool' if pool else 'service'
            prompt = f'Applying config to {noun} {service_name!r}. Proceed?'
            if prompt is not None:
                click.confirm(prompt,
                              default=True,
                              abort=True,
                              show_default=True)

        dag = client_common.upload_mounts_to_api_server(dag)
        dag_str = dag_utils.dump_chain_dag_to_yaml_str(dag)

        body = payloads.JobsPoolApplyBody(
            task=dag_str,
            pool_name=service_name,
            mode=mode,
        )
        response = server_common.make_authenticated_request(
            'POST',
            '/jobs/pool_apply',
            json=json.loads(body.model_dump_json()),
            timeout=(5, None))
        return server_common.get_request_id(response)


def down(
    service_names: Optional[Union[str, List[str]]],
    all: bool = False,  # pylint: disable=redefined-builtin
    purge: bool = False,
    pool: bool = False,
) -> server_common.RequestId:
    if pool:
        body = payloads.JobsPoolDownBody(
            pool_names=service_names,
            all=all,
            purge=purge,
        )
    else:
        body = payloads.ServeDownBody(
            service_names=service_names,
            all=all,
            purge=purge,
        )
    response = server_common.make_authenticated_request(
        'POST',
        '/jobs/pool_down' if pool else '/serve/down',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None))
    return server_common.get_request_id(response)


def status(
    service_names: Optional[Union[str, List[str]]],
    pool: bool = False,
) -> server_common.RequestId:
    if pool:
        body = payloads.JobsPoolStatusBody(pool_names=service_names)
    else:
        body = payloads.ServeStatusBody(service_names=service_names)
    response = server_common.make_authenticated_request(
        'POST',
        '/jobs/pool_status' if pool else '/serve/status',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None))
    return server_common.get_request_id(response)
