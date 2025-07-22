"""SkyServe core APIs."""
import pathlib
import signal
import threading
import typing
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from sky import backends
from sky import exceptions
from sky import sky_logging
from sky.backends import backend_utils
from sky.serve import serve_utils
from sky.serve.server import impl
from sky.usage import usage_lib
from sky.utils import command_runner
from sky.utils import controller_utils
from sky.utils import rich_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import sky

logger = sky_logging.init_logger(__name__)


def _get_all_replica_targets(
    service_name: str, backend: backends.CloudVmRayBackend,
    handle: backends.CloudVmRayResourceHandle
) -> Set[serve_utils.ServiceComponentTarget]:
    """Helper function to get targets for all live replicas."""
    code = serve_utils.ServeCodeGen.get_service_status([service_name],
                                                       pool=False)
    returncode, serve_status_payload, stderr = backend.run_on_head(
        handle,
        code,
        require_outputs=True,
        stream_logs=False,
        separate_stderr=True)

    try:
        subprocess_utils.handle_returncode(returncode,
                                           code,
                                           'Failed to fetch services',
                                           stderr,
                                           stream_logs=True)
    except exceptions.CommandError as e:
        raise RuntimeError(e.error_msg) from e

    service_records = serve_utils.load_service_status(serve_status_payload)
    if not service_records:
        raise ValueError(f'Service {service_name!r} not found.')
    assert len(service_records) == 1
    service_record = service_records[0]

    return {
        serve_utils.ServiceComponentTarget(serve_utils.ServiceComponent.REPLICA,
                                           replica_info['replica_id'])
        for replica_info in service_record['replica_info']
    }


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
def update(
        task: 'sky.Task',
        service_name: str,
        mode: serve_utils.UpdateMode = serve_utils.DEFAULT_UPDATE_MODE) -> None:
    """Updates an existing service.

    Please refer to the sky.cli.serve_update for the document.

    Args:
        task: sky.Task to update.
        service_name: Name of the service.
        mode: Update mode.
    """
    return impl.update(task, service_name, mode, pool=False)


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

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend)

    code = serve_utils.ServeCodeGen.terminate_replica(service_name, replica_id,
                                                      purge)
    returncode, stdout, stderr = backend.run_on_head(handle,
                                                     code,
                                                     require_outputs=True,
                                                     stream_logs=False,
                                                     separate_stderr=True)

    try:
        subprocess_utils.handle_returncode(returncode,
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
    if isinstance(target, str):
        target = serve_utils.ServiceComponent(target)
    if not isinstance(target, serve_utils.ServiceComponent):
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'`target` must be a string or '
                             f'sky.serve.ServiceComponent, got {type(target)}.')

    if target == serve_utils.ServiceComponent.REPLICA:
        if replica_id is None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    '`replica_id` must be specified when using target=REPLICA.')
    else:
        if replica_id is not None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('`replica_id` must be None when using '
                                 'target=CONTROLLER/LOAD_BALANCER.')

    controller_type = controller_utils.Controllers.SKY_SERVE_CONTROLLER
    handle = backend_utils.is_controller_accessible(
        controller=controller_type,
        stopped_message=controller_type.value.default_hint_if_non_existent)

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend), backend

    if target != serve_utils.ServiceComponent.REPLICA:
        code = serve_utils.ServeCodeGen.stream_serve_process_logs(
            service_name,
            stream_controller=(
                target == serve_utils.ServiceComponent.CONTROLLER),
            follow=follow,
            tail=tail)
    else:
        assert replica_id is not None, service_name
        code = serve_utils.ServeCodeGen.stream_replica_logs(service_name,
                                                            replica_id,
                                                            follow,
                                                            tail=tail)

    # With the stdin=subprocess.DEVNULL, the ctrl-c will not directly
    # kill the process, so we need to handle it manually here.
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, backend_utils.interrupt_handler)
        signal.signal(signal.SIGTSTP, backend_utils.stop_handler)

    # Refer to the notes in
    # sky/backends/cloud_vm_ray_backend.py::CloudVmRayBackend::tail_logs.
    backend.run_on_head(handle,
                        code,
                        stream_logs=True,
                        process_stream=False,
                        ssh_mode=command_runner.SshMode.INTERACTIVE)


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
    # Step 0) get the controller handle
    with rich_utils.safe_status(
            ux_utils.spinner_message('Checking service status...')):
        controller_type = controller_utils.Controllers.SKY_SERVE_CONTROLLER
        handle = backend_utils.is_controller_accessible(
            controller=controller_type,
            stopped_message=controller_type.value.default_hint_if_non_existent)
        backend: backends.CloudVmRayBackend = (
            backend_utils.get_backend_from_handle(handle))

    requested_components: Set[serve_utils.ServiceComponent] = set()
    if not targets:
        # No targets specified -> request all components
        requested_components = {
            serve_utils.ServiceComponent.CONTROLLER,
            serve_utils.ServiceComponent.LOAD_BALANCER,
            serve_utils.ServiceComponent.REPLICA
        }
    else:
        # Parse provided targets
        if isinstance(targets, (str, serve_utils.ServiceComponent)):
            requested_components = {serve_utils.ServiceComponent(targets)}
        else:  # list
            requested_components = {
                serve_utils.ServiceComponent(t) for t in targets
            }

    normalized_targets: Set[serve_utils.ServiceComponentTarget] = set()
    if serve_utils.ServiceComponent.CONTROLLER in requested_components:
        normalized_targets.add(
            serve_utils.ServiceComponentTarget(
                serve_utils.ServiceComponent.CONTROLLER))
    if serve_utils.ServiceComponent.LOAD_BALANCER in requested_components:
        normalized_targets.add(
            serve_utils.ServiceComponentTarget(
                serve_utils.ServiceComponent.LOAD_BALANCER))
    if serve_utils.ServiceComponent.REPLICA in requested_components:
        with rich_utils.safe_status(
                ux_utils.spinner_message('Getting live replica infos...')):
            replica_targets = _get_all_replica_targets(service_name, backend,
                                                       handle)
        if not replica_ids:
            # Replica target requested but no specific IDs
            # -> Get all replica logs
            normalized_targets.update(replica_targets)
        else:
            # Replica target requested with specific IDs
            requested_replica_targets = [
                serve_utils.ServiceComponentTarget(
                    serve_utils.ServiceComponent.REPLICA, rid)
                for rid in replica_ids
            ]
            for target in requested_replica_targets:
                if target not in replica_targets:
                    logger.warning(f'Replica ID {target.replica_id} not found '
                                   f'for {service_name}. Skipping...')
                else:
                    normalized_targets.add(target)

    def sync_down_logs_by_target(target: serve_utils.ServiceComponentTarget):
        component = target.component
        # We need to set one side of the pipe to a logs stream, and the other
        # side to a file.
        log_path = str(pathlib.Path(local_dir) / f'{target}.log')
        stream_logs_code: str

        if component == serve_utils.ServiceComponent.CONTROLLER:
            stream_logs_code = (
                serve_utils.ServeCodeGen.stream_serve_process_logs(
                    service_name,
                    stream_controller=True,
                    follow=False,
                    tail=tail))
        elif component == serve_utils.ServiceComponent.LOAD_BALANCER:
            stream_logs_code = (
                serve_utils.ServeCodeGen.stream_serve_process_logs(
                    service_name,
                    stream_controller=False,
                    follow=False,
                    tail=tail))
        elif component == serve_utils.ServiceComponent.REPLICA:
            replica_id = target.replica_id
            assert replica_id is not None, service_name
            stream_logs_code = serve_utils.ServeCodeGen.stream_replica_logs(
                service_name, replica_id, follow=False, tail=tail)
        else:
            assert False, component

        # Refer to the notes in
        # sky/backends/cloud_vm_ray_backend.py::CloudVmRayBackend::tail_logs.
        backend.run_on_head(handle,
                            stream_logs_code,
                            stream_logs=False,
                            process_stream=False,
                            ssh_mode=command_runner.SshMode.INTERACTIVE,
                            log_path=log_path)

    subprocess_utils.run_in_parallel(sync_down_logs_by_target,
                                     list(normalized_targets))

    return local_dir
