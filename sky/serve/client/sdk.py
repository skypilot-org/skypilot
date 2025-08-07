"""SDK for SkyServe."""
import json
import typing
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from sky.serve.client import impl
from sky.server import common as server_common
from sky.server import rest
from sky.server.requests import payloads
from sky.usage import usage_lib
from sky.utils import context

if typing.TYPE_CHECKING:
    import io

    import sky
    from sky.serve import serve_utils


@context.contextual
@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def up(
    task: Union['sky.Task', 'sky.Dag'],
    service_name: str,
    # Internal only:
    # pylint: disable=invalid-name
    _need_confirmation: bool = False
) -> server_common.RequestId[Tuple[str, str]]:
    """Spins up a service.

    Please refer to the sky.cli.serve_up for the document.

    Args:
        task: sky.Task to serve up.
        service_name: Name of the service.
        _need_confirmation: (Internal only) Whether to show a confirmation
            prompt before spinning up the service.

    Returns:
        The request ID of the up request.

    Request Returns:
        service_name (str): The name of the service.  Same if passed in as an
            argument.
        endpoint (str): The service endpoint.
    """
    return impl.up(task,
                   service_name,
                   pool=False,
                   _need_confirmation=_need_confirmation)


@context.contextual
@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def update(
    task: Union['sky.Task', 'sky.Dag'],
    service_name: str,
    mode: 'serve_utils.UpdateMode',
    # Internal only:
    # pylint: disable=invalid-name
    _need_confirmation: bool = False
) -> server_common.RequestId[None]:
    """Updates an existing service.

    Please refer to the sky.cli.serve_update for the document.

    Args:
        task: sky.Task to update.
        service_name: Name of the service.
        mode: Update mode, including:
            - sky.serve.UpdateMode.ROLLING
            - sky.serve.UpdateMode.BLUE_GREEN
        _need_confirmation: (Internal only) Whether to show a confirmation
            prompt before updating the service.

    Returns:
        The request ID of the update request.

    Request Returns:
        None
    """
    return impl.update(task,
                       service_name,
                       mode,
                       pool=False,
                       _need_confirmation=_need_confirmation)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def down(
    service_names: Optional[Union[str, List[str]]],
    all: bool = False,  # pylint: disable=redefined-builtin
    purge: bool = False
) -> server_common.RequestId[None]:
    """Tears down a service.

    Please refer to the sky.cli.serve_down for the docs.

    Args:
        service_names: Name of the service(s).
        all: Whether to terminate all services.
        purge: Whether to terminate services in a failed status. These services
          may potentially lead to resource leaks.

    Returns:
        The request ID of the down request.

    Request Returns:
        None

    Request Raises:
        sky.exceptions.ClusterNotUpError: if the sky serve controller is not up.
        ValueError: if the arguments are invalid.
        RuntimeError: if failed to terminate the service.
    """
    return impl.down(service_names, all, purge, pool=False)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def terminate_replica(service_name: str, replica_id: int,
                      purge: bool) -> server_common.RequestId[None]:
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
    response = server_common.make_authenticated_request(
        'POST',
        '/serve/terminate-replica',
        json=json.loads(body.model_dump_json()),
        timeout=(5, None))
    return server_common.get_request_id(response)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def status(
    service_names: Optional[Union[str, List[str]]]
) -> server_common.RequestId[List[Dict[str, Any]]]:
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
            'endpoint': (Optional[str]) endpoint of the service,
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
        service_records (List[Dict[str, Any]]): A list of dicts, with each
            dict containing the information of a service. If a service is not
            found, it will be omitted from the returned list.

    Request Raises:
        RuntimeError: if failed to get the service status.
        exceptions.ClusterNotUpError: if the sky serve controller is not up.
    """
    return impl.status(service_names, pool=False)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
@rest.retry_transient_errors()
def tail_logs(service_name: str,
              target: Union[str, 'serve_utils.ServiceComponent'],
              replica_id: Optional[int] = None,
              follow: bool = True,
              output_stream: Optional['io.TextIOBase'] = None,
              tail: Optional[int] = None) -> None:
    """Tails logs for a service.

    Usage:

    .. code-block:: python

        sky.serve.tail_logs(
            service_name,
            target=<component>,
            follow=False, # Optionally, default to True
            # replica_id=3, # Must be specified when target is REPLICA.
        )


    ``target`` is a enum of ``sky.serve.ServiceComponent``, which can be one of:

    - ``sky.serve.ServiceComponent.CONTROLLER``

    - ``sky.serve.ServiceComponent.LOAD_BALANCER``

    - ``sky.serve.ServiceComponent.REPLICA``

    Pass target as a lower-case string is also supported, e.g.
    ``target='controller'``.
    To use ``sky.serve.ServiceComponent.REPLICA``, you must specify
    ``replica_id``.

    To tail controller logs:

    .. code-block:: python

        # follow default to True
        sky.serve.tail_logs(
            service_name, target=sky.serve.ServiceComponent.CONTROLLER
        )

    To print replica 3 logs:

    .. code-block:: python

        # Pass target as a lower-case string is also supported.
        sky.serve.tail_logs(
            service_name, target='replica',
            follow=False, replica_id=3
        )

    Args:
        service_name: Name of the service.
        target: The component to tail logs.
        replica_id: The ID of the replica to tail logs.
        follow: Whether to follow the logs.
        output_stream: The stream to write the logs to. If None, print to the
            console.

    Returns:
        The request ID of the tail logs request.

    Request Raises:
        sky.exceptions.ClusterNotUpError: the sky serve controller is not up.
        ValueError: arguments not valid, or failed to tail the logs.
    """
    return impl.tail_logs(service_name,
                          target,
                          replica_id,
                          follow,
                          output_stream,
                          tail,
                          pool=False)


@usage_lib.entrypoint
@server_common.check_server_healthy_or_start
def sync_down_logs(service_name: str,
                   local_dir: str,
                   *,
                   targets: Optional[Union[
                       str, 'serve_utils.ServiceComponent',
                       Sequence[Union[str,
                                      'serve_utils.ServiceComponent']]]] = None,
                   replica_ids: Optional[List[int]] = None,
                   tail: Optional[int] = None) -> None:
    """Sync down logs from the service components to a local directory.

    This function syncs logs from the specified service components (controller,
    load balancer, replicas) via the API server to a specified local directory.

    Args:
        service_name: The name of the service to download logs from.
        targets: Which component(s) to download logs for. If None or empty,
            means download all logs (controller, load-balancer, all replicas).
            Can be a string (e.g. "controller"), or a `ServiceComponent` object,
            or a list of them for multiple components. Currently accepted
            values:
                - "controller"/ServiceComponent.CONTROLLER
                - "load_balancer"/ServiceComponent.LOAD_BALANCER
                - "replica"/ServiceComponent.REPLICA
        replica_ids: The list of replica IDs to download logs from, specified
            when target includes `ServiceComponent.REPLICA`. If target includes
            `ServiceComponent.REPLICA` but this is None/empty, logs for all
            replicas will be downloaded.
        local_dir: Local directory to sync down logs to. Defaults to
            `~/sky_logs`.

    Raises:
        RuntimeError: If fails to gather logs or fails to rsync from the
          controller.
        sky.exceptions.ClusterNotUpError: If the controller is not up.
        ValueError: Arguments not valid.
    """
    return impl.sync_down_logs(service_name,
                               local_dir,
                               targets=targets,
                               replica_ids=replica_ids,
                               tail=tail,
                               pool=False)
