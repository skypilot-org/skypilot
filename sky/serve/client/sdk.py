"""SDK for SkyServe."""
import json
import typing
from typing import List, Optional, Union

import click
import requests

from sky.server import common as server_common
from sky.server.requests import payloads
from sky.usage import usage_lib
from sky.utils import dag_utils

if typing.TYPE_CHECKING:
    import sky
    from sky.serve import serve_utils


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def up(task: Union['sky.Task', 'sky.Dag'],
       service_name: str,
       need_confirmation: bool = False) -> server_common.RequestId:
    """Spins up a service.

    Please refer to the sky.cli.serve_up for the document.

    Args:
        task: sky.Task to serve up.
        service_name: Name of the service.
        need_confirmation: Whether to show a confirmation prompt before spinning
            up the service.

    Returns:
        The request ID of the up request.

    Request Returns:
        service_name: str; The name of the service.  Same if passed in as an
            argument.
        endpoint: str; The service endpoint.
    """
    # This is to avoid circular import.
    from sky.client import sdk  # pylint: disable=import-outside-toplevel
    dag = dag_utils.convert_entrypoint_to_dag(task)
    sdk.validate(dag)
    request_id = sdk.optimize(dag)
    sdk.stream_and_get(request_id)
    if need_confirmation:
        prompt = f'Launching a new service {service_name!r}. Proceed?'
        if prompt is not None:
            click.confirm(prompt, default=True, abort=True, show_default=True)

    dag = server_common.upload_mounts_to_api_server(dag)
    dag_str = dag_utils.dump_chain_dag_to_yaml_str(dag)

    body = payloads.ServeUpBody(
        task=dag_str,
        service_name=service_name,
    )
    response = requests.post(
        f'{server_common.get_server_url()}/serve/up',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
    )
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def update(task: Union['sky.Task', 'sky.Dag'],
           service_name: str,
           mode: 'serve_utils.UpdateMode',
           need_confirmation: bool = False) -> server_common.RequestId:
    """Updates an existing service.

    Please refer to the sky.cli.serve_update for the document.

    Args:
        task: sky.Task to update.
        service_name: Name of the service.
        mode: Update mode, including:
            - ROLLING
            - BLUE_GREEN
        need_confirmation: Whether to show a confirmation prompt before updating
            the service.

    Returns:
        The request ID of the update request.

    Request Returns: None
    """
    # This is to avoid circular import.
    from sky.client import sdk  # pylint: disable=import-outside-toplevel
    dag = dag_utils.convert_entrypoint_to_dag(task)
    sdk.validate(dag)
    request_id = sdk.optimize(dag)
    sdk.stream_and_get(request_id)
    if need_confirmation:
        click.confirm(f'Updating service {service_name!r}. Proceed?',
                      default=True,
                      abort=True,
                      show_default=True)

    dag = server_common.upload_mounts_to_api_server(dag)
    dag_str = dag_utils.dump_chain_dag_to_yaml_str(dag)
    body = payloads.ServeUpdateBody(
        task=dag_str,
        service_name=service_name,
        mode=mode,
    )

    response = requests.post(
        f'{server_common.get_server_url()}/serve/update',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
    )
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def down(
    service_names: Optional[Union[str, List[str]]],
    all: bool = False,  # pylint: disable=redefined-builtin
    purge: bool = False
) -> server_common.RequestId:
    """Tears down a service.

    Please refer to the sky.cli.serve_down for the docs.

    Args:
        service_names: Name of the service(s).
        all: Whether to terminate all services.
        purge: Whether to terminate services in a failed status. These services
          may potentially lead to resource leaks.

    Request Raises:
        sky.exceptions.ClusterNotUpError: if the sky serve controller is not up.
        ValueError: if the arguments are invalid.
        RuntimeError: if failed to terminate the service.
    """
    body = payloads.ServeDownBody(
        service_names=service_names,
        all=all,
        purge=purge,
    )
    response = requests.post(
        f'{server_common.get_server_url()}/serve/down',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
    )
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def terminate_replica(service_name: str, replica_id: int,
                      purge: bool) -> server_common.RequestId:
    """Tears down a specific replica for the given service.

    Args:
        service_name: Name of the service.
        replica_id: ID of replica to terminate.
        purge: Whether to terminate replicas in a failed status. These replicas
          may lead to resource leaks, so we require the user to explicitly
          specify this flag to make sure they are aware of this potential
          resource leak.

    Returns:
        The request ID of the terminate replica request.

    Request Raises:
        sky.exceptions.ClusterNotUpError: if the sky sere controller is not up.
        RuntimeError: if failed to terminate the replica.
    """
    body = payloads.ServeTerminateReplicaBody(
        service_name=service_name,
        replica_id=replica_id,
        purge=purge,
    )
    response = requests.post(
        f'{server_common.get_server_url()}/serve/terminate-replica',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
    )
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def status(
        service_names: Optional[Union[str,
                                      List[str]]]) -> server_common.RequestId:
    """Gets service statuses.

    If service_names is given, return those services. Otherwise, return all
    services.

    Each returned value has the following fields:

    .. code-block:: python

        {
            'name': (str) service name,
            'active_versions': (List[int]) a list of versions that are active,
            'controller_job_id': (int) the job id of the controller,
            'uptime': (int) uptime in seconds,
            'status': (sky.ServiceStatus) service status,
            'controller_port': (Optional[int]) controller port,
            'load_balancer_port': (Optional[int]) load balancer port,
            'policy': (Optional[str]) autoscaling policy description,
            'requested_resources_str': (str) str representation of
              requested resources,
            'load_balancing_policy': (str) load balancing policy name,
            'replica_info': (List[Dict[str, Any]]) replica information,
        }

    Each entry in replica_info has the following fields:

    .. code-block:: python

        {
            'replica_id': (int) replica id,
            'name': (str) replica name,
            'status': (sky.serve.ReplicaStatus) replica status,
            'version': (int) replica version,
            'launched_at': (int) timestamp of launched,
            'handle': (ResourceHandle) handle of the replica cluster,
            'endpoint': (str) endpoint of the replica,
        }

    For possible service statuses and replica statuses, please refer to
    sky.cli.serve_status.

    Args:
        service_names: a single or a list of service names to query. If None,
            query all services.

    Returns:
        The request ID of the status request.

    Request Returns:
        A list of dicts, with each dict containing the information of a service.
        If a service is not found, it will be omitted from the returned list.

    Request Raises:
        RuntimeError: if failed to get the service status.
        exceptions.ClusterNotUpError: if the sky serve controller is not up.
    """
    body = payloads.ServeStatusBody(service_names=service_names,)
    response = requests.post(
        f'{server_common.get_server_url()}/serve/status',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
    )
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def tail_logs(service_name: str,
              target: Union[str, 'serve_utils.ServiceComponent'],
              replica_id: Optional[int] = None,
              follow: bool = True) -> server_common.RequestId:
    """Tails logs for a service.

    Usage:
        sky.stream_and_get(sky.serve.tail_logs(
            service_name,
            target=<component>,
            follow=False, # Optionally, default to True
            # replica_id=3, # Must be specified when target is REPLICA.
        ))

    `target` is a enum of sky.serve.ServiceComponent, which can be one of:
        - CONTROLLER
        - LOAD_BALANCER
        - REPLICA
    Pass target as a lower-case string is also supported, e.g.
    target='controller'.
    To use REPLICA, you must specify `replica_id`.

    To tail controller logs:
        # follow default to True
        sky.stream_and_get(sky.serve.tail_logs(
            service_name, target=sky.serve.ServiceComponent.CONTROLLER))

    To print replica 3 logs:
        # Pass target as a lower-case string is also supported.
        sky.stream_and_get(sky.serve.tail_logs(
            service_name, target='replica',
            follow=False, replica_id=3)

    Request Raises:
        sky.exceptions.ClusterNotUpError: the sky serve controller is not up.
        ValueError: arguments not valid, or failed to tail the logs.
    """
    body = payloads.ServeLogsBody(
        service_name=service_name,
        target=target,
        replica_id=replica_id,
        follow=follow,
    )
    response = requests.post(
        f'{server_common.get_server_url()}/serve/logs',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None),
    )
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def endpoint(port: Optional[Union[int, str]] = None) -> server_common.RequestId:
    """Gets the endpoint for the serve controller.

    Args:
        port: The port number to get the endpoint for. If None, endpoints
            for all ports are returned..

    Returns:
        The request ID of the endpoint request.

    Request Returns: A dictionary of port numbers to endpoints. If endpoint is
        None, the dictionary will contain all ports:endpoints exposed on the
        controller.

    Request Raises:
        ValueError: if the controller is not UP or the endpoint is not exposed.
        RuntimeError: if the controller has no ports to be exposed or no
            endpoints are exposed yet.
    """
    body = payloads.ServeEndpointBody(port=port,)
    response = requests.post(f'{server_common.get_server_url()}/serve/endpoint',
                             json=json.loads(body.model_dump_json()))
    return server_common.get_request_id(response)
