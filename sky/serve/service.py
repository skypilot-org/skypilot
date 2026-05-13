"""Main entrypoint to start a service.

This including the controller and load balancer.
"""
import argparse
import multiprocessing
import os
import pathlib
import shutil
import socket
import time
import traceback
from typing import Any, Dict, Optional

import filelock

from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import task as task_lib
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.data import data_utils
from sky.serve import constants
from sky.serve import controller
from sky.serve import load_balancer
from sky.serve import replica_managers
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.skylet import constants as skylet_constants
from sky.utils import auth_utils
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import subprocess_utils
from sky.utils import thread_utils
from sky.utils import ux_utils

# Use the explicit logger name so that the logger is under the
# `sky.serve.service` namespace when executed directly, so as
# to inherit the setup from the `sky` logger.
logger = sky_logging.init_logger('sky.serve.service')


def _handle_signal(service_name: str) -> None:
    """Handles the signal user sent to controller."""
    signal_file = pathlib.Path(
        constants.SIGNAL_FILE_PATH.format(service_name)).expanduser()
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


def cleanup_storage(yaml_content: str) -> bool:
    """Clean up the storage for the service.

    Args:
        yaml_content: The yaml content of the service.

    Returns:
        True if the storage is cleaned up successfully, False otherwise.
    """
    failed = False
    task = None

    try:
        task = task_lib.Task.from_yaml_str(yaml_content)
        backend = cloud_vm_ray_backend.CloudVmRayBackend()
        # Need to re-construct storage object in the controller process
        # because when SkyPilot API server machine sends the yaml config to the
        # controller machine, only storage metadata is sent, not the storage
        # object itself.
        # Construct storages individually so a stale reference (bucket
        # already deleted by a prior cleanup pass) doesn't abort cleanup of
        # the rest. StorageBucketGetError on construct here means the bucket
        # is already gone — which IS the cleanup target state, not a failure.
        # Without this, FAILED_CLEANUP becomes self-perpetuating: the loop
        # crashes on a stale storage entry, the service goes to
        # FAILED_CLEANUP, ha_recovery_for_consolidation_mode respawns the
        # controller, the controller re-reads the same yaml and crashes on
        # the same stale entry — forever.
        for storage_name in list(task.storage_mounts.keys()):
            storage = task.storage_mounts[storage_name]
            try:
                storage.construct()
            except exceptions.StorageBucketGetError as e:
                logger.debug(f'cleanup_storage: bucket for storage '
                             f'{storage_name!r} already gone, treating as '
                             f'already cleaned: {e}')
                del task.storage_mounts[storage_name]
        backend.teardown_ephemeral_storage(task)
    except Exception as e:  # pylint: disable=broad-except
        logger.error('Failed to clean up storage: '
                     f'{common_utils.format_exception(e)}')
        with ux_utils.enable_traceback():
            logger.error(f'  Traceback: {traceback.format_exc()}')
        failed = True

    # Clean up any files mounted from the local disk, such as two-hop file
    # mounts. Guard against `task` being unbound if from_yaml_str raised above.
    file_mount_values = (list(
        (task.file_mounts or {}).values()) if task else [])
    for file_mount in file_mount_values:
        try:
            if not data_utils.is_cloud_store_url(file_mount):
                path = os.path.expanduser(file_mount)
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'Failed to clean up file mount {file_mount}: {e}')
            with ux_utils.enable_traceback():
                logger.error(f'  Traceback: {traceback.format_exc()}')
            failed = True

    return not failed


# NOTE(dev): We don't need to acquire the `with_lock` in replica manager here
# because we killed all the processes (controller & replica manager) before
# calling this function.
def _cleanup(service_name: str, pool: bool) -> bool:
    """Clean up all service related resources, i.e. replicas and storage."""
    # Cleanup the HA recovery script first as it is possible that some error
    # was raised when we construct the task object (e.g.,
    # sky.exceptions.ResourcesUnavailableError).
    serve_state.remove_ha_recovery_script(service_name)
    failed = False
    replica_infos = serve_state.get_replica_infos(service_name)
    info2thr: Dict[replica_managers.ReplicaInfo,
                   thread_utils.SafeThread] = dict()
    # NOTE(dev): This relies on `sky/serve/serve_utils.py::
    # generate_replica_cluster_name`. Change it if you change the function.
    existing_cluster_names = global_user_state.get_cluster_names_start_with(
        service_name)
    for info in replica_infos:
        if info.cluster_name not in existing_cluster_names:
            logger.info(f'Cluster {info.cluster_name} for replica '
                        f'{info.replica_id} not found. Might be a failed '
                        'cluster. Removing replica from database.')
            try:
                serve_state.remove_replica(service_name, info.replica_id)
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(f'Failed to remove replica {info.replica_id} '
                               f'from database: {e}')
                failed = True
            continue

        log_file_name = serve_utils.generate_replica_log_file_name(
            service_name, info.replica_id)
        t = thread_utils.SafeThread(target=replica_managers.terminate_cluster,
                                    args=(info.cluster_name, log_file_name))
        info2thr[info] = t
        # Set replica status to `SHUTTING_DOWN`
        info.status_property.sky_launch_status = (
            replica_managers.common_utils.ProcessStatus.SUCCEEDED)
        info.status_property.sky_down_status = (
            replica_managers.common_utils.ProcessStatus.SCHEDULED)
        serve_state.add_or_update_replica(service_name, info.replica_id, info)
        logger.info(f'Scheduling to terminate replica {info.replica_id} ...')

    def _set_to_failed_cleanup(info: replica_managers.ReplicaInfo) -> None:
        nonlocal failed
        # Set replica status to `FAILED_CLEANUP`
        info.status_property.sky_down_status = (
            replica_managers.common_utils.ProcessStatus.FAILED)
        serve_state.add_or_update_replica(service_name, info.replica_id, info)
        failed = True
        logger.error(f'Replica {info.replica_id} failed to terminate.')

    # Please reference to sky/serve/replica_managers.py::_refresh_process_pool.
    # TODO(tian): Refactor to use the same logic and code.
    while info2thr:
        snapshot = list(info2thr.items())
        for info, t in snapshot:
            if t.is_alive():
                continue
            if (info.status_property.sky_down_status ==
                    replica_managers.common_utils.ProcessStatus.SCHEDULED):
                if controller_utils.can_terminate(pool):
                    try:
                        t.start()
                    except Exception as e:  # pylint: disable=broad-except
                        _set_to_failed_cleanup(info)
                        logger.error(f'Failed to start thread for replica '
                                     f'{info.replica_id}: {e}')
                        del info2thr[info]
                    else:
                        info.status_property.sky_down_status = (
                            common_utils.ProcessStatus.RUNNING)
                        serve_state.add_or_update_replica(
                            service_name, info.replica_id, info)
            else:
                logger.info('Terminate thread for replica '
                            f'{info.replica_id} finished.')
                t.join()
                del info2thr[info]
                if t.format_exc is None:
                    serve_state.remove_replica(service_name, info.replica_id)
                    logger.info(
                        f'Replica {info.replica_id} terminated successfully.')
                else:
                    _set_to_failed_cleanup(info)
        time.sleep(3)

    def cleanup_version_storage(version: int) -> bool:
        yaml_content = serve_state.get_yaml_content(service_name, version)
        if yaml_content is None:
            logger.warning(f'No yaml content found for version {version}')
            return True
        logger.info(f'Cleaning up storage for version {version}, '
                    f'yaml_content: {yaml_content}')
        return cleanup_storage(yaml_content)

    versions = serve_state.get_service_versions(service_name)
    if not all(map(cleanup_version_storage, versions)):
        failed = True

    # NOTE: do not delete version_specs here. The success path in `_start`
    # deletes them along with `remove_service`. Deleting them on failure
    # makes the `services` row invisible to `get_service_from_name` (it
    # uses an INNER JOIN with `version_specs`), so `sky ... status` /
    # `sky ... down --purge` can no longer locate the FAILED_CLEANUP row,
    # and the only way out is to manually delete the DB row.
    return failed


def _cleanup_task_run_script(job_id: int) -> None:
    """Clean up task run script.
    Please see `kubernetes-ray.yml.j2` for more details.
    """
    task_run_dir = pathlib.Path(
        skylet_constants.PERSISTENT_RUN_SCRIPT_DIR).expanduser()
    if task_run_dir.exists():
        this_task_run_script = task_run_dir / f'sky_job_{job_id}'
        if this_task_run_script.exists():
            this_task_run_script.unlink()
            logger.info(f'Task run script {this_task_run_script} removed')
        else:
            logger.warning(f'Task run script {this_task_run_script} not found')


def _wait_for_controller_ready(host: str, port: int, timeout: int = 30) -> None:
    """Block until the controller HTTP server is accepting connections.

    We must not flip DB `controller_pid`/`controller_ip` until the new
    subprocess is actually listening, otherwise clients routed by DB hit
    the new pod's IP before its uvicorn binds and get ECONNREFUSED.
    """
    # When binding 0.0.0.0, probe via loopback.
    probe_host = '127.0.0.1' if host == '0.0.0.0' else host
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((probe_host, port), timeout=0.5):
                return
        except (ConnectionRefusedError, OSError):
            time.sleep(0.2)
    raise RuntimeError(f'Controller did not become ready on '
                       f'{probe_host}:{port} within {timeout}s')


def _orphan_exit(
        controller_process: Optional[multiprocessing.Process],
        load_balancer_process: Optional[multiprocessing.Process]) -> None:
    """Quick exit path for an orphan sky.serve.service.

    Triggered when our self-check sees DB `controller_pid` no longer matches
    our own pid (a newer instance on another pod has taken over) or when the
    services row has been removed (down completed). DB state is now owned by
    that new instance — we must NOT call _cleanup, which would teardown
    replicas and delete versions, racing with the new owner.

    Just kill our own forked subprocesses and exit immediately.
    """
    logger.info(
        f'_orphan_exit invoked: own_pid={os.getpid()} '
        f'controller_process_pid='
        f'{controller_process.pid if controller_process else None} '
        f'load_balancer_process_pid='
        f'{load_balancer_process.pid if load_balancer_process else None}')
    process_to_kill = [
        proc for proc in [load_balancer_process, controller_process]
        if proc is not None
    ]
    if process_to_kill:
        try:
            subprocess_utils.kill_children_processes(
                parent_pids=[p.pid for p in process_to_kill], force=True)
        except Exception:  # pylint: disable=broad-except
            logger.warning('Failed to kill children during orphan exit; '
                           'proceeding with os._exit anyway')
    # os._exit() bypasses the try/finally which would call _cleanup.
    os._exit(0)  # pylint: disable=protected-access


def _start(service_name: str, tmp_task_yaml: str, job_id: int, entrypoint: str):
    """Starts the service.
    This including the controller and load balancer.
    """
    # Generate ssh key pair to avoid race condition when multiple sky.launch
    # are executed at the same time.
    auth_utils.get_or_generate_keys()

    service = serve_state.get_service_from_name(service_name)
    is_recovery = service is not None
    logger.info(f'It is a {"first" if not is_recovery else "recovery"} run')

    def _read_yaml_content(yaml_path: str) -> str:
        with open(os.path.expanduser(yaml_path), 'r', encoding='utf-8') as f:
            return f.read()

    if is_recovery:
        assert service is not None
        yaml_content = service['yaml_content']
        # Backward compatibility for old service records that
        # does not dump the yaml content to version database.
        # TODO(tian): Remove this after 2 minor releases, i.e. 0.13.0.
        if yaml_content is None:
            yaml_content = _read_yaml_content(tmp_task_yaml)
    else:
        yaml_content = _read_yaml_content(tmp_task_yaml)

    # Initialize database record for the service.
    task = task_lib.Task.from_yaml_str(yaml_content)
    # Already checked before submit to controller.
    assert task.service is not None, task
    service_spec = task.service

    service_dir = os.path.expanduser(
        serve_utils.generate_remote_service_dir_name(service_name))

    # Pod IP for HA leader-aware routing.
    pod_ip: Optional[str] = os.environ.get('POD_IP')

    if not is_recovery:
        with filelock.FileLock(controller_utils.get_resources_lock_path()):
            if not controller_utils.can_start_new_process(task.service.pool):
                cleanup_storage(yaml_content)
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(
                        controller_utils.get_max_services_error_message(
                            task.service.pool))
            success = serve_state.add_service(
                service_name,
                controller_job_id=job_id,
                policy=service_spec.autoscaling_policy_str(),
                requested_resources_str=backend_utils.get_task_resources_str(
                    task),
                load_balancing_policy=service_spec.load_balancing_policy,
                status=serve_state.ServiceStatus.CONTROLLER_INIT,
                tls_encrypted=service_spec.tls_credential is not None,
                pool=service_spec.pool,
                controller_pid=os.getpid(),
                controller_ip=pod_ip,
                entrypoint=entrypoint)
        # Directly throw an error here. See sky/serve/api.py::up
        # for more details.
        if not success:
            cleanup_storage(yaml_content)
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Service {service_name} already exists.')

        # Create the service working directory.
        os.makedirs(service_dir, exist_ok=True)

        version = constants.INITIAL_VERSION
        # Add initial version information to the service state.
        serve_state.add_or_update_version(service_name, version, service_spec,
                                          yaml_content)
    else:
        latest_version = serve_state.get_latest_version(service_name)
        if latest_version is None:
            raise ValueError(f'No version found for service {service_name}')
        version = latest_version
        # NOTE: do NOT update controller_pid/controller_ip yet. We must wait
        # until our subprocess is actually listening on the port (see the
        # update_service_controller_pid_ip_and_port call after
        # _wait_for_controller_ready below). Otherwise clients in other pods
        # would route to our pod IP before uvicorn binds and get
        # ECONNREFUSED for the duration of the boot.

    controller_process = None
    load_balancer_process = None
    try:
        with filelock.FileLock(
                os.path.expanduser(constants.PORT_SELECTION_FILE_LOCK_PATH)):
            # Start the controller.
            # NOTE: also pick a fresh free port on recovery — do NOT reuse
            # the port from DB. The port in DB was chosen on the previous
            # controller's pod (e.g. Pod A); on a different recovery pod
            # (Pod B), that port may be in use by another service's
            # controller, in which case our subprocess would fail to bind
            # → _wait_for_controller_ready times out → daemon retries
            # forever. Picking locally guarantees the port is free *on this
            # pod*, and the post-bind atomic flip writes the new port to
            # DB together with pid/ip so clients route correctly.
            controller_port = common_utils.find_free_port(
                constants.CONTROLLER_PORT_START)

            def _get_controller_host():
                """Get the controller host address.
                We expose the controller to the public network when running
                inside a kubernetes cluster to allow external load balancers
                (example, for high availability load balancers) to communicate
                with the controller.
                """
                if 'KUBERNETES_SERVICE_HOST' in os.environ:
                    return '0.0.0.0'
                # Not using localhost to avoid using ipv6 address and causing
                # the following error:
                # ERROR:    [Errno 99] error while attempting to bind on address
                # ('::1', 20001, 0, 0): cannot assign requested address
                return '127.0.0.1'

            controller_host = _get_controller_host()
            controller_process = multiprocessing.Process(
                target=controller.run_controller,
                args=(service_name, service_spec, version, controller_host,
                      controller_port))
            controller_process.start()
            logger.debug(f'_start() spawned controller_process pid='
                         f'{controller_process.pid} host={controller_host} '
                         f'port={controller_port}')

            # NOTE: do NOT write controller_port to DB before the subprocess
            # actually binds. If the spawn races with another service for
            # the same port and our subprocess fails to bind, we don't want
            # DB to advertise an unbound port to clients. Move the DB write
            # to after `_wait_for_controller_ready` succeeds (below).

            # Wait for the uvicorn server inside the controller subprocess to
            # be listening before we (potentially) flip DB to point at us.
            # This makes the recovery's DB update atomic from the client's
            # perspective: DB either still points at the previous controller
            # (which is alive) or it points at us (which is now ready).
            try:
                _wait_for_controller_ready(
                    controller_host,
                    controller_port,
                    timeout=constants.SERVICE_REGISTER_TIMEOUT_SECONDS)
            except RuntimeError:
                # Controller subprocess failed to start. Bail; the daemon's
                # next ha_recovery iteration will retry.
                logger.error('Controller subprocess failed to start within '
                             f'{constants.SERVICE_REGISTER_TIMEOUT_SECONDS}s; '
                             'aborting _start. DB state untouched so the '
                             'previous controller (if any) keeps serving.')
                raise

            # Now we know the subprocess is bound on `controller_port`. Write
            # DB.
            if is_recovery:
                # Atomic flip: DB now points at us; clients route here. We're
                # ready to serve.
                logger.debug(f'is_recovery: flipping DB controller_pid '
                             f'-> {os.getpid()}, controller_ip -> {pod_ip}, '
                             f'controller_port -> {controller_port}')
                serve_state.update_service_controller_pid_ip_and_port(
                    service_name,
                    controller_pid=os.getpid(),
                    controller_ip=pod_ip,
                    controller_port=controller_port)
            else:
                # Fresh up: add_service already wrote pid/ip with the row.
                # Only the port needs to land in DB now (after bind).
                serve_state.set_service_controller_port(service_name,
                                                        controller_port)

            controller_addr = f'http://{controller_host}:{controller_port}'

            # Start the load balancer.
            load_balancer_port = (
                common_utils.find_free_port(constants.LOAD_BALANCER_PORT_START)
                if not is_recovery else
                serve_state.get_service_load_balancer_port(service_name))
            load_balancer_log_file = os.path.expanduser(
                serve_utils.generate_remote_load_balancer_log_file_name(
                    service_name))

            # TODO(tian): Probably we could enable multiple ports specified in
            # service spec and we could start multiple load balancers.
            # After that, we will have a mapping from replica port to endpoint.
            # NOTE(tian): We don't need the load balancer for pool.
            # Skip the load balancer process for pool.
            if not service_spec.pool:
                load_balancer_process = multiprocessing.Process(
                    target=ux_utils.RedirectOutputForProcess(
                        load_balancer.run_load_balancer,
                        load_balancer_log_file).run,
                    args=(controller_addr, load_balancer_port,
                          service_spec.load_balancing_policy,
                          service_spec.tls_credential,
                          service_spec.target_qps_per_replica))
                load_balancer_process.start()

            if not is_recovery:
                serve_state.set_service_load_balancer_port(
                    service_name, load_balancer_port)

        # Self-check cadence (seconds): how often we re-read DB to confirm
        # we're still the authoritative controller. Ghost detection only
        # matters in HA deployments and is checked once per
        # interval to avoid DB load.
        orphan_check_interval_seconds = 30
        own_pid = os.getpid()
        loop_count = 0
        while True:
            _handle_signal(service_name)
            loop_count += 1
            # Periodically check whether we still own the row in DB. If
            # another instance on a different pod has taken over (HA recovery
            # raced us to the row), or if down has removed the row entirely,
            # exit immediately without running cleanup — that work belongs to
            # the new owner / has already happened.
            if loop_count % orphan_check_interval_seconds == 0:
                db_read_failed = False
                record: Optional[Dict[str, Any]] = None
                try:
                    record = serve_state.get_service_from_name(service_name)
                except Exception:  # pylint: disable=broad-except
                    # DB transient failure — keep running, retry next tick.
                    db_read_failed = True
                if not db_read_failed and record is None:
                    logger.warning(
                        f'Service {service_name} row no longer present in '
                        'DB. Exiting as orphan without running cleanup.')
                    _orphan_exit(controller_process, load_balancer_process)
                elif (record is not None and
                      record.get('controller_pid') is not None and
                      record.get('controller_pid') != own_pid):
                    logger.warning(
                        f'Service {service_name} controller_pid in DB is '
                        f'{record.get("controller_pid")} but our pid is '
                        f'{own_pid}; another instance has taken over. '
                        'Exiting as orphan without running cleanup.')
                    _orphan_exit(controller_process, load_balancer_process)
            time.sleep(1)
    except exceptions.ServeUserTerminatedError:
        logger.debug(f'Caught ServeUserTerminatedError for '
                     f'{service_name}; setting status=SHUTTING_DOWN')
        serve_state.set_service_status_and_active_versions(
            service_name, serve_state.ServiceStatus.SHUTTING_DOWN)
    finally:
        # Kill load balancer process first since it will raise errors if failed
        # to connect to the controller. Then the controller process.
        process_to_kill = [
            proc for proc in [load_balancer_process, controller_process]
            if proc is not None
        ]
        subprocess_utils.kill_children_processes(
            parent_pids=[process.pid for process in process_to_kill],
            force=True)
        for process in process_to_kill:
            process.join()

        # Catch any exception here to avoid it kill the service monitoring
        # process. In which case, the service will not only fail to clean
        # up, but also cannot be terminated in the future as no process
        # will handle the user signal anymore. Instead, we catch any error
        # and set it to FAILED_CLEANUP instead.
        try:
            failed = _cleanup(service_name, service_spec.pool)
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'Failed to clean up service {service_name}: {e}')
            with ux_utils.enable_traceback():
                logger.error(f'  Traceback: {traceback.format_exc()}')
            failed = True

        if failed:
            serve_state.set_service_status_and_active_versions(
                service_name, serve_state.ServiceStatus.FAILED_CLEANUP)
            logger.error(f'Service {service_name} failed to clean up.')
        else:
            serve_state.remove_service_completely(service_name)
            try:
                shutil.rmtree(service_dir)
            except FileNotFoundError:
                # The service_dir may already be gone (e.g. the controller's own
                # success path raced with a purge).
                pass
            logger.info(f'Service {service_name} terminated successfully.')

        _cleanup_task_run_script(job_id)


if __name__ == '__main__':
    logger.info('Starting service...')

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
    parser.add_argument('--entrypoint',
                        type=str,
                        help='Entrypoint to launch the service',
                        required=True)
    args = parser.parse_args()
    # We start process with 'spawn', because 'fork' could result in weird
    # behaviors; 'spawn' is also cross-platform.
    multiprocessing.set_start_method('spawn', force=True)
    _start(args.service_name, args.task_yaml, args.job_id, args.entrypoint)
