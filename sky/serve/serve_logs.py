"""Sync down logs"""
import os
from typing import Optional

from sky import backends
from sky import exceptions
from sky import serve as serve_lib
from sky import sky_logging
from sky.backends import backend_utils
from sky.backends.cloud_vm_ray_backend import CloudVmRayResourceHandle
from sky.serve import constants as serve_constants
from sky.serve import serve_utils
from sky.skylet import constants
from sky.utils import command_runner
from sky.utils import subprocess_utils

logger = sky_logging.init_logger(__name__)


def sync_down_serve_logs(
    backend,
    controller_handle: CloudVmRayResourceHandle,
    service_name: str,
    service_component: Optional[serve_lib.ServiceComponent],
    replica_id: Optional[int],
) -> None:
    """Sync down serve logs. Will sync down logs for controller, load
        balancer, replica, or everything.

        Args:
            controller_handle: The handle to the sky serve controller.
            service_name: The name of the service.
            service_component: The component to sync down the logs of.
            Could be controller, load balancer, replica, or None.
            None means to sync down logs for everything.
            replica_id: The replica ID to tail the logs of. Only used when
            target is replica.
    """
    if service_component is None:
        assert replica_id is None
    ssh_credentials = backend_utils.ssh_credential_from_yaml(
        controller_handle.cluster_yaml, controller_handle.docker_user)
    runner = command_runner.SSHCommandRunner(
        node=(controller_handle.head_ip, controller_handle.head_ssh_port),
        **ssh_credentials,
    )
    sky_logs_directory = os.path.expanduser(constants.SKY_LOGS_DIRECTORY)
    run_timestamp = backend_utils.get_run_timestamp()
    sync_down_all_components = service_component is None

    if (sync_down_all_components or
            service_component == serve_utils.ServiceComponent.REPLICA):
        prepare_code = (
            serve_utils.ServeCodeGen.prepare_replica_logs_for_download(
                service_name, run_timestamp, replica_id))
        backend = backend_utils.get_backend_from_handle(controller_handle)
        assert isinstance(backend, backends.CloudVmRayBackend)
        assert isinstance(controller_handle, backends.CloudVmRayResourceHandle)
        logger.info('Preparing replica logs for download on controller...')
        prepare_returncode = backend.run_on_head(controller_handle,
                                                 prepare_code,
                                                 require_outputs=False,
                                                 stream_logs=False)
        try:
            subprocess_utils.handle_returncode(
                prepare_returncode, prepare_code,
                'Failed to prepare replica logs to sync down. '
                'Sky serve controller may be older and not support sync '
                'down for replica logs.')
        except exceptions.CommandError as e:
            raise RuntimeError(e.error_msg) from e
        remote_service_dir_name = (
            serve_utils.generate_remote_service_dir_name(service_name))
        dir_for_download = os.path.join(remote_service_dir_name, run_timestamp)
        logger.info('Downloading the replica logs...')
        runner.rsync(source=dir_for_download,
                     target=sky_logs_directory,
                     up=False,
                     stream_logs=False)
        remove_code = (
            serve_utils.ServeCodeGen.remove_replica_logs_for_download(
                service_name, run_timestamp))
        remove_returncode = backend.run_on_head(controller_handle,
                                                remove_code,
                                                require_outputs=False,
                                                stream_logs=False)
        try:
            subprocess_utils.handle_returncode(
                remove_returncode, remove_code,
                'Failed to remove the replica logs for '
                'download on the controller.')
        except exceptions.CommandError as e:
            raise RuntimeError(e.error_msg) from e

    # We download the replica logs first because that download creates
    # the timestamp directory, and we can just put the controller and
    # load balancer logs in there. Otherwise, we would create the
    # timestamp directory with controller and load balancer logs first,
    # and then the replica logs download would overwrite it.
    target_directory = os.path.join(sky_logs_directory, run_timestamp)
    os.makedirs(target_directory, exist_ok=True)
    if (sync_down_all_components or
            service_component == serve_utils.ServiceComponent.CONTROLLER):
        controller_log_file_name = (
            serve_utils.generate_remote_controller_log_file_name(service_name))
        logger.info('Downloading the controller logs...')
        runner.rsync(source=controller_log_file_name,
                     target=os.path.join(
                         target_directory,
                         serve_constants.CONTROLLER_LOG_FILE_NAME),
                     up=False,
                     stream_logs=False)
    if (sync_down_all_components or
            service_component == serve_utils.ServiceComponent.LOAD_BALANCER):
        load_balancer_log_file_name = (
            serve_utils.generate_remote_load_balancer_log_file_name(
                service_name))
        logger.info('Downloading the load balancer logs...')
        runner.rsync(source=load_balancer_log_file_name,
                     target=os.path.join(
                         target_directory,
                         serve_constants.LOAD_BALANCER_LOG_FILE_NAME),
                     up=False,
                     stream_logs=False)
    logger.info(f'Synced down logs can be found at: {target_directory}')
