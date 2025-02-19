"""Main entrypoint to start a service.

This including the controller and load balancer.
"""
import argparse
import multiprocessing
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
import traceback
import typing
from typing import Any, Dict, Optional

import filelock

from sky import authentication
from sky import exceptions
from sky import global_user_state
from sky import resources as resources_lib
from sky import sky_logging
from sky import task as task_lib
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.serve import constants
from sky.serve import controller
from sky.serve import load_balancer
from sky.serve import replica_managers
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.skylet import constants as skylet_constants
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.serve import service_spec

# Use the explicit logger name so that the logger is under the
# `sky.serve.service` namespace when executed directly, so as
# to inherit the setup from the `sky` logger.
logger = sky_logging.init_logger('sky.serve.service')


def _handle_signal(service_name: str) -> None:
    """Handles the signal user sent to controller."""
    signal_file = pathlib.Path(constants.SIGNAL_FILE_PATH.format(service_name))
    user_signal = None
    if signal_file.exists():
        # Filelock is needed to prevent race condition with concurrent
        # signal writing.
        with filelock.FileLock(str(signal_file) + '.lock'):
            with signal_file.open(mode='r', encoding='utf-8') as f:
                user_signal_text = f.read().strip()
                try:
                    user_signal = serve_utils.UserSignal(user_signal_text)
                    logger.info(f'User signal received: {user_signal}')
                except ValueError:
                    logger.warning(
                        f'Unknown signal received: {user_signal}. Ignoring.')
                    user_signal = None
            # Remove the signal file, after reading it.
            signal_file.unlink()
    if user_signal is None:
        return
    assert isinstance(user_signal, serve_utils.UserSignal)
    error_type = user_signal.error_type()
    raise error_type(f'User signal received: {user_signal.value}')


def cleanup_storage(task_yaml: str) -> bool:
    """Clean up the storage for the service.

    Args:
        task_yaml: The task yaml file.

    Returns:
        True if the storage is cleaned up successfully, False otherwise.
    """
    try:
        task = task_lib.Task.from_yaml(task_yaml)
        backend = cloud_vm_ray_backend.CloudVmRayBackend()
        # Need to re-construct storage object in the controller process
        # because when SkyPilot API server machine sends the yaml config to the
        # controller machine, only storage metadata is sent, not the storage
        # object itself.
        for storage in task.storage_mounts.values():
            storage.construct()
        backend.teardown_ephemeral_storage(task)
    except Exception as e:  # pylint: disable=broad-except
        logger.error('Failed to clean up storage: '
                     f'{common_utils.format_exception(e)}')
        with ux_utils.enable_traceback():
            logger.error(f'  Traceback: {traceback.format_exc()}')
        return False
    return True


def _get_cluster_ip(cluster_name: str) -> Optional[str]:
    record = global_user_state.get_cluster_from_name(cluster_name)
    if record is None:
        return None
    if record['handle'].head_ip is None:
        return None
    return record['handle'].head_ip


def _get_domain_name(subdomain: str, hosted_zone: str) -> str:
    return f'{subdomain}.{hosted_zone}'


def _get_route53_change(action: str, subdomain: str, hosted_zone: str,
                        record_type: str, region: str,
                        value: str) -> Dict[str, Any]:
    return {
        'Action': action,
        'ResourceRecordSet': {
            'Name': _get_domain_name(subdomain, hosted_zone),
            'Type': record_type,
            'TTL': 300,
            'Region': region,
            'SetIdentifier': f'{subdomain}-{region}',
            'ResourceRecords': [{
                'Value': value
            }]
        }
    }


def _cleanup(service_name: str,
             service_spec: 'service_spec.SkyServiceSpec') -> bool:
    """Clean up all service related resources, i.e. replicas and storage."""
    failed = False
    hosted_zone = service_spec.route53_hosted_zone

    replica_infos = serve_state.get_replica_infos(service_name)
    info2proc: Dict[replica_managers.ReplicaInfo,
                    multiprocessing.Process] = dict()
    external_lbs = serve_state.get_external_load_balancers(service_name)
    lbid2proc: Dict[int, multiprocessing.Process] = dict()
    for info in replica_infos:
        p = multiprocessing.Process(target=replica_managers.terminate_cluster,
                                    args=(info.cluster_name,))
        p.start()
        info2proc[info] = p
        # Set replica status to `SHUTTING_DOWN`
        info.status_property.sky_launch_status = (
            replica_managers.ProcessStatus.SUCCEEDED)
        info.status_property.sky_down_status = (
            replica_managers.ProcessStatus.RUNNING)
        serve_state.add_or_update_replica(service_name, info.replica_id, info)
        logger.info(f'Terminating replica {info.replica_id} ...')
    change_batch = []
    for external_lb_record in external_lbs:
        lb_cluster_name = external_lb_record['cluster_name']
        lb_id = external_lb_record['lb_id']
        lb_region = external_lb_record['region']
        p = multiprocessing.Process(target=replica_managers.terminate_cluster,
                                    args=(lb_cluster_name,))
        p.start()
        lbid2proc[lb_id] = p
        lb_ip = _get_cluster_ip(lb_cluster_name)
        assert lb_ip is not None
        # Hosted zone must be set for external LBs.
        assert hosted_zone is not None
        change_batch.append(
            _get_route53_change('DELETE', service_name, hosted_zone, 'A',
                                lb_region, lb_ip))
        logger.info(f'Terminating external load balancer {lb_cluster_name} ...')

    if change_batch:
        # TODO(tian): Fix this import hack.
        import boto3  # pylint: disable=import-outside-toplevel
        client = boto3.client('route53')
        client.change_resource_record_sets(
            HostedZoneId=service_spec.target_hosted_zone_id,
            ChangeBatch={'Changes': change_batch})

    for info, p in info2proc.items():
        p.join()
        if p.exitcode == 0:
            serve_state.remove_replica(service_name, info.replica_id)
            logger.info(f'Replica {info.replica_id} terminated successfully.')
        else:
            # Set replica status to `FAILED_CLEANUP`
            info.status_property.sky_down_status = (
                replica_managers.ProcessStatus.FAILED)
            serve_state.add_or_update_replica(service_name, info.replica_id,
                                              info)
            failed = True
            logger.error(f'Replica {info.replica_id} failed to terminate.')
    for lb_id, p in lbid2proc.items():
        p.join()
        if p.exitcode == 0:
            serve_state.remove_external_load_balancer(service_name, lb_id)
            logger.info(
                f'External load balancer {lb_id} terminated successfully.')
        else:
            failed = True
            logger.error(f'External load balancer {lb_id} failed to terminate.')
    versions = serve_state.get_service_versions(service_name)
    serve_state.remove_service_versions(service_name)

    def cleanup_version_storage(version: int) -> bool:
        task_yaml: str = serve_utils.generate_task_yaml_file_name(
            service_name, version)
        logger.info(f'Cleaning up storage for version {version}, '
                    f'task_yaml: {task_yaml}')
        return cleanup_storage(task_yaml)

    if not all(map(cleanup_version_storage, versions)):
        failed = True

    return failed


def _get_external_lb_cluster_name(service_name: str, lb_id: int) -> str:
    return f'sky-{service_name}-lb-{lb_id}'


def _start_external_load_balancer(service_name: str, lb_id: int,
                                  lb_cluster_name: str, controller_addr: str,
                                  lb_port: int, lb_policy: str,
                                  lb_resources: Dict[str, Any]) -> None:
    # TODO(tian): Hack. We should figure out the optimal resoruces.
    if 'cpus' not in lb_resources:
        lb_resources['cpus'] = '2+'
    # Already checked in service spec validation.
    assert 'ports' not in lb_resources
    lb_resources['ports'] = [lb_port]
    lbr = resources_lib.Resources.from_yaml_config(lb_resources)
    # TODO(tian): Set delete=False to debug. Remove this on production.
    with tempfile.NamedTemporaryFile(prefix=lb_cluster_name,
                                     mode='w',
                                     delete=False) as f:
        vars_to_fill = {
            'load_balancer_port': lb_port,
            'controller_addr': controller_addr,
            'load_balancing_policy': lb_policy,
            'sky_activate_python_env':
                skylet_constants.ACTIVATE_SKY_REMOTE_PYTHON_ENV,
            'lb_envs': controller_utils.sky_managed_cluster_envs(),
        }
        common_utils.fill_template(constants.EXTERNAL_LB_TEMPLATE,
                                   vars_to_fill,
                                   output_path=f.name)
        lb_task = task_lib.Task.from_yaml(f.name)
        lb_task.set_resources(lbr)
        serve_state.add_external_load_balancer(service_name, lb_id,
                                               lb_cluster_name,
                                               lb_resources['region'], lb_port)
        # TODO(tian): Temporary solution for circular import. We should move
        # the import to the top of the file.
        import sky  # pylint: disable=import-outside-toplevel
        sky.launch(
            task=lb_task,
            cluster_name=lb_cluster_name,
            retry_until_up=True,
        )


def _start(service_name: str, tmp_task_yaml: str, job_id: int):
    """Starts the service."""
    # Generate ssh key pair to avoid race condition when multiple sky.launch
    # are executed at the same time.
    authentication.get_or_generate_keys()

    # Initialize database record for the service.
    task = task_lib.Task.from_yaml(tmp_task_yaml)
    # Already checked before submit to controller.
    assert task.service is not None, task
    service_spec = task.service
    if len(serve_state.get_services()) >= serve_utils.NUM_SERVICE_THRESHOLD:
        cleanup_storage(tmp_task_yaml)
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError('Max number of services reached.')
    success = serve_state.add_service(
        service_name,
        controller_job_id=job_id,
        policy=service_spec.autoscaling_policy_str(),
        requested_resources_str=backend_utils.get_task_resources_str(task),
        load_balancing_policy=service_spec.load_balancing_policy,
        status=serve_state.ServiceStatus.CONTROLLER_INIT,
        tls_encrypted=service_spec.tls_credential is not None)
    # Directly throw an error here. See sky/serve/api.py::up
    # for more details.
    if not success:
        cleanup_storage(tmp_task_yaml)
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Service {service_name} already exists.')

    # Add initial version information to the service state.
    serve_state.add_or_update_version(service_name, constants.INITIAL_VERSION,
                                      service_spec)

    # Create the service working directory.
    service_dir = os.path.expanduser(
        serve_utils.generate_remote_service_dir_name(service_name))
    os.makedirs(service_dir, exist_ok=True)

    # Copy the tmp task yaml file to the final task yaml file.
    # This is for the service name conflict case. The _execute will
    # sync file mounts first and then realized a name conflict. We
    # don't want the new file mounts to overwrite the old one, so we
    # sync to a tmp file first and then copy it to the final name
    # if there is no name conflict.
    task_yaml = serve_utils.generate_task_yaml_file_name(
        service_name, constants.INITIAL_VERSION)
    shutil.copy(tmp_task_yaml, task_yaml)

    controller_process = None
    load_balancer_processes = []
    try:
        with filelock.FileLock(
                os.path.expanduser(constants.PORT_SELECTION_FILE_LOCK_PATH)):
            controller_port = common_utils.find_free_port(
                constants.CONTROLLER_PORT_START)

            # We expose the controller to the public network when running
            # inside a kubernetes cluster to allow external load balancers
            # (example, for high availability load balancers) to communicate
            # with the controller.
            # Also, when we are using external load balancers, in which we
            # need to get the information from a distinct machine.
            def _get_host():
                if ('KUBERNETES_SERVICE_HOST' in os.environ or
                        service_spec.external_load_balancers is not None):
                    return '0.0.0.0'
                # Not using localhost to avoid using ipv6 address and causing
                # the following error:
                # ERROR:    [Errno 99] error while attempting to bind on address
                # ('::1', 20001, 0, 0): cannot assign requested address
                return '127.0.0.1'

            def _get_external_host():
                assert service_spec.external_load_balancers is not None
                # TODO(tian): Use a more robust way to get the host.
                return subprocess.check_output(
                    'curl ifconfig.me', shell=True).decode('utf-8').strip()

            controller_host = _get_host()

            # Start the controller.
            controller_process = multiprocessing.Process(
                target=controller.run_controller,
                args=(service_name, service_spec, task_yaml, controller_host,
                      controller_port))
            controller_process.start()
            serve_state.set_service_controller_port(service_name,
                                                    controller_port)

            controller_addr = f'http://{controller_host}:{controller_port}'
            # TODO(tian): Combine the following two.
            lbid2cluster = {}
            lbid2region = {}

            if service_spec.external_load_balancers is None:
                # Generate load balancer log file name.
                load_balancer_log_file = os.path.expanduser(
                    serve_utils.generate_remote_load_balancer_log_file_name(
                        service_name))

                load_balancer_port = common_utils.find_free_port(
                    constants.LOAD_BALANCER_PORT_START)

                # Extract the load balancing policy from the service spec
                policy_name = service_spec.load_balancing_policy

                # Start the load balancer.
                # TODO(tian): Probably we could enable multiple ports specified
                # in service spec and we could start multiple load balancers.
                # After that, we need a mapping from replica port to endpoint.
                load_balancer_process = multiprocessing.Process(
                    target=ux_utils.RedirectOutputForProcess(
                        load_balancer.run_load_balancer,
                        load_balancer_log_file).run,
                    args=(controller_addr, load_balancer_port, policy_name,
                          service_spec.tls_credential))
                load_balancer_process.start()
                serve_state.set_service_load_balancer_port(
                    service_name, load_balancer_port)
            else:
                for lb_id, lb_config in enumerate(
                        service_spec.external_load_balancers):
                    # Generate load balancer log file name.
                    load_balancer_log_file = os.path.expanduser(
                        serve_utils.
                        generate_remote_external_load_balancer_log_file_name(
                            service_name, lb_id))
                    lb_cluster_name = _get_external_lb_cluster_name(
                        service_name, lb_id)
                    lbid2cluster[lb_id] = lb_cluster_name
                    lb_policy = lb_config['load_balancing_policy']
                    lb_resources = lb_config['resources']
                    lbid2region[lb_id] = lb_resources['region']
                    controller_external_addr = (
                        f'http://{_get_external_host()}:{controller_port}')
                    lb_process = multiprocessing.Process(
                        target=ux_utils.RedirectOutputForProcess(
                            _start_external_load_balancer,
                            load_balancer_log_file).run,
                        # TODO(tian): Support HTTPS on external load balancer.
                        # TODO(tian): Let the user to customize the port.
                        # TODO(tian): Or, default to port 80 (need root).
                        args=(service_name, lb_id, lb_cluster_name,
                              controller_external_addr,
                              constants.EXTERNAL_LB_PORT, lb_policy,
                              lb_resources))
                    lb_process.start()
                    load_balancer_processes.append(lb_process)

        if service_spec.external_load_balancers is not None:
            hosted_zone = service_spec.route53_hosted_zone
            if hosted_zone is not None:
                # Wait for the LBs is ready, get the IPs and setup Route53.
                while True:
                    if all(
                            _get_cluster_ip(lb_cluster_name) is not None
                            for lb_cluster_name in lbid2cluster.values()):
                        break
                    time.sleep(1)
                # TODO(tian): Fix this import hack.
                import boto3  # pylint: disable=import-outside-toplevel
                client = boto3.client('route53')
                change_batch = []
                for lb_id, lb_cluster_name in lbid2cluster.items():
                    lb_ip = _get_cluster_ip(lb_cluster_name)
                    assert lb_ip is not None
                    change_batch.append(
                        _get_route53_change('CREATE', service_name, hosted_zone,
                                            'A', lbid2region[lb_id], lb_ip))
                client.change_resource_record_sets(
                    HostedZoneId=service_spec.target_hosted_zone_id,
                    ChangeBatch={'Changes': change_batch})
                serve_state.set_service_dns_endpoint(
                    service_name, _get_domain_name(service_name, hosted_zone))
            serve_state.set_service_load_balancer_port(
                service_name, constants.EXTERNAL_LB_PORT)

        while True:
            _handle_signal(service_name)
            time.sleep(1)
    except exceptions.ServeUserTerminatedError:
        serve_state.set_service_status_and_active_versions(
            service_name, serve_state.ServiceStatus.SHUTTING_DOWN)
    finally:
        # Kill load balancer process first since it will raise errors if failed
        # to connect to the controller. Then the controller process.
        process_to_kill = [
            proc for proc in [*load_balancer_processes, controller_process]
            if proc is not None
        ]
        subprocess_utils.kill_children_processes(
            parent_pids=[process.pid for process in process_to_kill],
            force=True)
        for process in process_to_kill:
            process.join()
        failed = _cleanup(service_name, service_spec)
        if failed:
            serve_state.set_service_status_and_active_versions(
                service_name, serve_state.ServiceStatus.FAILED_CLEANUP)
            logger.error(f'Service {service_name} failed to clean up.')
        else:
            shutil.rmtree(service_dir)
            serve_state.remove_service(service_name)
            serve_state.delete_all_versions(service_name)
            logger.info(f'Service {service_name} terminated successfully.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Sky Serve Service')
    parser.add_argument('--service-name',
                        type=str,
                        help='Name of the service',
                        required=True)
    parser.add_argument('--task-yaml',
                        type=str,
                        help='Task YAML file',
                        required=True)
    parser.add_argument('--job-id',
                        required=True,
                        type=int,
                        help='Job id for the service job.')
    args = parser.parse_args()
    # We start process with 'spawn', because 'fork' could result in weird
    # behaviors; 'spawn' is also cross-platform.
    multiprocessing.set_start_method('spawn', force=True)
    _start(args.service_name, args.task_yaml, args.job_id)
