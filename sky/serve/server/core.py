"""SkyServe core APIs."""
import typing
from typing import Any, Dict, List, Optional, Tuple, Union

from sky import backends
from sky import exceptions
from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.backends import backend_utils
from sky.serve import serve_rpc_utils
from sky.serve import serve_utils
from sky.serve.server import impl
from sky.usage import usage_lib
from sky.utils import controller_utils
from sky.utils import subprocess_utils

if typing.TYPE_CHECKING:
    import grpc

    import sky
else:
    grpc = adaptors_common.LazyImport('grpc')

logger = sky_logging.init_logger(__name__)


@usage_lib.entrypoint
def up(
    task: 'sky.Task',
    service_name: Optional[str] = None,
) -> Tuple[str, str]:
    """Spins up a service.

    Please refer to the sky.cli.serve_up for the document.

    Args:
        task: sky.Task to serve up.
        service_name: Name of the service.

    Returns:
        service_name: str; The name of the service.  Same if passed in as an
            argument.
        endpoint: str; The service endpoint.
    """
    return impl.up(task, service_name, pool=False)


@usage_lib.entrypoint
def update(task: Optional['sky.Task'],
           service_name: str,
           mode: serve_utils.UpdateMode = serve_utils.DEFAULT_UPDATE_MODE,
           workers: Optional[int] = None) -> None:
    """Updates an existing service.

    Please refer to the sky.cli.serve_update for the document.

    Args:
        task: sky.Task to update, or None if updating
            the number of workers/replicas.
        service_name: Name of the service.
        mode: Update mode.
        workers: Number of workers/replicas to set for the service when
            task is None.
    """
    return impl.update(task, service_name, mode, pool=False, workers=workers)


@usage_lib.entrypoint
# pylint: disable=redefined-builtin
def down(
    service_names: Optional[Union[str, List[str]]] = None,
    all: bool = False,
    purge: bool = False,
) -> None:
    """Tears down a service.

    Please refer to the sky.cli.serve_down for the docs.

    Args:
        service_names: Name of the service(s).
        all: Whether to terminate all services.
        purge: Whether to terminate services in a failed status. These services
          may potentially lead to resource leaks.

    Raises:
        sky.exceptions.ClusterNotUpError: if the sky serve controller is not up.
        ValueError: if the arguments are invalid.
        RuntimeError: if failed to terminate the service.
    """
    return impl.down(service_names, all, purge, pool=False)


@usage_lib.entrypoint
def terminate_replica(service_name: str, replica_id: int, purge: bool) -> None:
    """Tears down a specific replica for the given service.

    Args:
        service_name: Name of the service.
        replica_id: ID of replica to terminate.
        purge: Whether to terminate replicas in a failed status. These replicas
          may lead to resource leaks, so we require the user to explicitly
          specify this flag to make sure they are aware of this potential
          resource leak.

    Raises:
        sky.exceptions.ClusterNotUpError: if the sky sere controller is not up.
        RuntimeError: if failed to terminate the replica.
    """
    handle = backend_utils.is_controller_accessible(
        controller=controller_utils.Controllers.SKY_SERVE_CONTROLLER,
        stopped_message=
        'No service is running now. Please spin up a service first.',
        non_existent_message='No service is running now. '
        'Please spin up a service first.',
    )

    assert isinstance(handle, backends.CloudVmRayResourceHandle)
    use_legacy = not handle.is_grpc_enabled_with_flag

    if not use_legacy:
        try:
            stdout = serve_rpc_utils.RpcRunner.terminate_replica(
                handle, service_name, replica_id, purge)
        except exceptions.SkyletMethodNotImplementedError:
            use_legacy = True

    if use_legacy:
        backend = backend_utils.get_backend_from_handle(handle)
        assert isinstance(backend, backends.CloudVmRayBackend)

        code = serve_utils.ServeCodeGen.terminate_replica(
            service_name, replica_id, purge)
        returncode, stdout, stderr = backend.run_on_head(handle,
                                                         code,
                                                         require_outputs=True,
                                                         stream_logs=False,
                                                         separate_stderr=True)

        try:
            subprocess_utils.handle_returncode(
                returncode,
                code,
                'Failed to terminate the replica',
                stderr,
                stream_logs=True)
        except exceptions.CommandError as e:
            raise RuntimeError(e.error_msg) from e

    sky_logging.print(stdout)


@usage_lib.entrypoint
def status(
    service_names: Optional[Union[str,
                                  List[str]]] = None) -> List[Dict[str, Any]]:
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
            'endpoint': (Optional[str]) load balancer endpoint,
            'policy': (Optional[str]) autoscaling policy description,
            'requested_resources_str': (str) str representation of
              requested resources,
            'load_balancing_policy': (str) load balancing policy name,
            'tls_encrypted': (bool) whether the service is TLS encrypted,
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
        A list of dicts, with each dict containing the information of a service.
        If a service is not found, it will be omitted from the returned list.

    Raises:
        RuntimeError: if failed to get the service status.
        exceptions.ClusterNotUpError: if the sky serve controller is not up.
    """
    return impl.status(service_names, pool=False)


ServiceComponentOrStr = Union[str, serve_utils.ServiceComponent]


@usage_lib.entrypoint
def tail_logs(
    service_name: str,
    *,
    target: ServiceComponentOrStr,
    replica_id: Optional[int] = None,
    follow: bool = True,
    tail: Optional[int] = None,
) -> None:
    """Tails logs for a service.

    Usage:
        sky.serve.tail_logs(
            service_name,
            target=<component>,
            follow=False, # Optionally, default to True
            # replica_id=3, # Must be specified when target is REPLICA.
        )

    `target` is a enum of sky.serve.ServiceComponent, which can be one of:
        - CONTROLLER
        - LOAD_BALANCER
        - REPLICA
    Pass target as a lower-case string is also supported, e.g.
    target='controller'.
    To use REPLICA, you must specify `replica_id`.

    To tail controller logs:
        # follow default to True
        sky.serve.tail_logs(
            service_name, target=sky.serve.ServiceComponent.CONTROLLER)

    To print replica 3 logs:
        # Pass target as a lower-case string is also supported.
        sky.serve.tail_logs(
            service_name, target='replica',
            follow=False, replica_id=3)

    Raises:
        sky.exceptions.ClusterNotUpError: the sky serve controller is not up.
        ValueError: arguments not valid, or failed to tail the logs.
    """
    return impl.tail_logs(service_name,
                          target=target,
                          replica_id=replica_id,
                          follow=follow,
                          tail=tail,
                          pool=False)


@usage_lib.entrypoint
def sync_down_logs(
    service_name: str,
    *,
    local_dir: str,
    targets: Union[ServiceComponentOrStr, List[ServiceComponentOrStr],
                   None] = None,
    replica_ids: Optional[List[int]] = None,
    tail: Optional[int] = None,
) -> str:
    """Sync down logs from the controller for the given service.

    This function is called by the server endpoint. It gathers logs from the
    controller, load balancer, and/or replicas and places them in a directory
    under the user's log space on the API server filesystem.

    Args:
        service_name: The name of the service to download logs from.
        local_dir: The local directory to save the logs to.
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

    Returns:
        A dict mapping component names to local paths where the logs were synced
        down to.

    Raises:
        RuntimeError: If fails to gather logs or fails to rsync from the
          controller.
        sky.exceptions.ClusterNotUpError: If the controller is not up.
        ValueError: Arguments not valid.
    """
    return impl.sync_down_logs(service_name,
                               local_dir=local_dir,
                               targets=targets,
                               replica_ids=replica_ids,
                               tail=tail,
                               pool=False)
