"""Setup dependencies & services for instances."""
from concurrent import futures
import functools
import hashlib
import json
import os
import resource
import time
from typing import Any, Dict, List, Optional, Tuple

from sky import provision
from sky import sky_logging
from sky.provision import common
from sky.provision import docker_utils
from sky.provision import logging as provision_logging
from sky.provision import metadata_utils
from sky.skylet import constants
from sky.utils import accelerator_registry
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)
_START_TITLE = '\n' + '-' * 20 + 'Start: {} ' + '-' * 20
_END_TITLE = '-' * 20 + 'End:   {} ' + '-' * 20 + '\n'

_MAX_RETRY = 6

# Increase the limit of the number of open files for the raylet process,
# as the `ulimit` may not take effect at this point, because it requires
# all the sessions to be reloaded. This is a workaround.
_RAY_PRLIMIT = (
    'which prlimit && for id in $(pgrep -f raylet/raylet); '
    'do sudo prlimit --nofile=1048576:1048576 --pid=$id || true; done;')

_DUMP_RAY_PORTS = (
    f'{constants.SKY_PYTHON_CMD} -c \'import json, os; '
    f'json.dump({constants.SKY_REMOTE_RAY_PORT_DICT_STR}, '
    f'open(os.path.expanduser("{constants.SKY_REMOTE_RAY_PORT_FILE}"), "w", '
    'encoding="utf-8"))\';')

_RAY_PORT_COMMAND = (
    f'RAY_PORT=$({constants.SKY_PYTHON_CMD} -c '
    '"from sky.skylet import job_lib; print(job_lib.get_ray_port())" '
    '2> /dev/null || echo 6379);'
    f'{constants.SKY_PYTHON_CMD} -c "from sky.utils import common_utils; '
    'print(common_utils.encode_payload({\'ray_port\': $RAY_PORT}))"')

# Command that calls `ray status` with SkyPilot's Ray port set.
RAY_STATUS_WITH_SKY_RAY_PORT_COMMAND = (
    f'{_RAY_PORT_COMMAND}; '
    f'RAY_ADDRESS=127.0.0.1:$RAY_PORT {constants.SKY_RAY_CMD} status')

# Command that waits for the ray status to be initialized. Otherwise, a later
# `sky status -r` may fail due to the ray cluster not being ready.
RAY_HEAD_WAIT_INITIALIZED_COMMAND = (
    f'while `{constants.RAY_STATUS} | grep -q "No cluster status."`; do '
    'sleep 0.5; '
    'echo "Waiting ray cluster to be initialized"; '
    'done;')

# Restart skylet when the version does not match to keep the skylet up-to-date.
MAYBE_SKYLET_RESTART_CMD = (f'{constants.SKY_PYTHON_CMD} -m '
                            'sky.skylet.attempt_skylet;')


def _auto_retry(func):
    """Decorator that retries the function if it fails.

    This decorator is mostly for SSH disconnection issues, which might happen
    during the setup of instances.
    """

    @functools.wraps(func)
    def retry(*args, **kwargs):
        backoff = common_utils.Backoff(initial_backoff=1, max_backoff_factor=5)
        for retry_cnt in range(_MAX_RETRY):
            try:
                return func(*args, **kwargs)
            except Exception as e:  # pylint: disable=broad-except
                if retry_cnt >= _MAX_RETRY - 1:
                    raise e
                sleep = backoff.current_backoff()
                logger.info(
                    f'{func.__name__}: Retrying in {sleep:.1f} seconds, '
                    f'due to {e}')
                time.sleep(sleep)

    return retry


def _log_start_end(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logger.info(_START_TITLE.format(func.__name__))
        try:
            return func(*args, **kwargs)
        finally:
            logger.info(_END_TITLE.format(func.__name__))

    return wrapper


def _hint_worker_log_path(cluster_name: str, cluster_info: common.ClusterInfo,
                          stage_name: str):
    if cluster_info.num_instances > 1:
        worker_log_path = metadata_utils.get_instance_log_dir(
            cluster_name, '*') / (stage_name + '.log')
        logger.info(f'Logs of worker nodes can be found at: {worker_log_path}')


def _parallel_ssh_with_cache(func,
                             cluster_name: str,
                             stage_name: str,
                             digest: Optional[str],
                             cluster_info: common.ClusterInfo,
                             ssh_credentials: Dict[str, Any],
                             max_workers: Optional[int] = None) -> List[Any]:
    if max_workers is None:
        # Not using the default value of `max_workers` in ThreadPoolExecutor,
        # as 32 is too large for some machines.
        max_workers = subprocess_utils.get_parallel_threads()
    with futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = []
        runners = provision.get_command_runners(cluster_info.provider_name,
                                                cluster_info, **ssh_credentials)
        # instance_ids is guaranteed to be in the same order as runners.
        instance_ids = cluster_info.instance_ids()
        for i, runner in enumerate(runners):
            cache_id = instance_ids[i]
            wrapper = metadata_utils.cache_func(cluster_name, cache_id,
                                                stage_name, digest)
            if i == 0:
                # Log the head node's output to the provision.log
                log_path_abs = str(provision_logging.get_log_path())
            else:
                log_dir_abs = metadata_utils.get_instance_log_dir(
                    cluster_name, cache_id)
                log_path_abs = str(log_dir_abs / (stage_name + '.log'))
            results.append(pool.submit(wrapper(func), runner, log_path_abs))

        return [future.result() for future in results]


@_log_start_end
def initialize_docker(cluster_name: str, docker_config: Dict[str, Any],
                      cluster_info: common.ClusterInfo,
                      ssh_credentials: Dict[str, Any]) -> Optional[str]:
    """Setup docker on the cluster."""
    if not docker_config:
        return None
    _hint_worker_log_path(cluster_name, cluster_info, 'initialize_docker')

    @_auto_retry
    def _initialize_docker(runner: command_runner.CommandRunner, log_path: str):
        docker_user = docker_utils.DockerInitializer(docker_config, runner,
                                                     log_path).initialize()
        logger.debug(f'Initialized docker user: {docker_user}')
        return docker_user

    docker_users = _parallel_ssh_with_cache(
        _initialize_docker,
        cluster_name,
        stage_name='initialize_docker',
        # Should not cache docker setup, as it needs to be
        # run every time a cluster is restarted.
        digest=None,
        cluster_info=cluster_info,
        ssh_credentials=ssh_credentials)
    logger.debug(f'All docker users: {docker_users}')
    assert len(set(docker_users)) == 1, docker_users
    return docker_users[0]


@_log_start_end
def setup_runtime_on_cluster(cluster_name: str, setup_commands: List[str],
                             cluster_info: common.ClusterInfo,
                             ssh_credentials: Dict[str, Any]) -> None:
    """Setup internal dependencies."""
    _hint_worker_log_path(cluster_name, cluster_info,
                          'setup_runtime_on_cluster')
    # compute the digest
    digests = []
    for cmd in setup_commands:
        digests.append(hashlib.sha256(cmd.encode()).digest())
    hasher = hashlib.sha256()
    for d in digests:
        hasher.update(d)
    digest = hasher.hexdigest()

    @_auto_retry
    def _setup_node(runner: command_runner.CommandRunner, log_path: str):
        for cmd in setup_commands:
            returncode, stdout, stderr = runner.run(
                cmd,
                stream_logs=False,
                log_path=log_path,
                require_outputs=True,
                # Installing depencies requires source bashrc to access the PATH
                # in bashrc.
                source_bashrc=True)
            retry_cnt = 0
            while returncode == 255 and retry_cnt < _MAX_RETRY:
                # Got network connection issue occur during setup. This could
                # happen when a setup step requires a reboot, e.g. nvidia-driver
                # installation (happens for fluidstack). We should retry for it.
                logger.info('Network connection issue during setup, this is '
                            'likely due to the reboot of the instance. '
                            'Retrying setup in 10 seconds.')
                time.sleep(10)
                retry_cnt += 1
                returncode, stdout, stderr = runner.run(cmd,
                                                        stream_logs=False,
                                                        log_path=log_path,
                                                        require_outputs=True,
                                                        source_bashrc=True)
                if not returncode:
                    break

            if returncode:
                raise RuntimeError(
                    'Failed to run setup commands on an instance. '
                    f'(exit code {returncode}). Error: '
                    f'===== stdout ===== \n{stdout}\n'
                    f'===== stderr ====={stderr}')

    _parallel_ssh_with_cache(_setup_node,
                             cluster_name,
                             stage_name='setup_runtime_on_cluster',
                             digest=digest,
                             cluster_info=cluster_info,
                             ssh_credentials=ssh_credentials)


def _ray_gpu_options(custom_resource: str) -> str:
    """Returns GPU options for the ray start command.

    For some cases (e.g., within docker container), we need to explicitly set
    --num-gpus to have ray clusters recognize the schedulable GPUs.
    """
    acc_dict = json.loads(custom_resource)
    assert len(acc_dict) == 1, acc_dict
    acc_name, acc_count = list(acc_dict.items())[0]
    if accelerator_registry.is_schedulable_non_gpu_accelerator(acc_name):
        return ''
    # We need to manually set the number of GPUs, as it may not automatically
    # detect the GPUs within the container.
    return f' --num-gpus={acc_count}'


@_log_start_end
@_auto_retry
def start_ray_on_head_node(cluster_name: str, custom_resource: Optional[str],
                           cluster_info: common.ClusterInfo,
                           ssh_credentials: Dict[str, Any]) -> None:
    """Start Ray on the head node."""
    runners = provision.get_command_runners(cluster_info.provider_name,
                                            cluster_info, **ssh_credentials)
    head_runner = runners[0]
    assert cluster_info.head_instance_id is not None, (cluster_name,
                                                       cluster_info)

    # Log the head node's output to the provision.log
    log_path_abs = str(provision_logging.get_log_path())
    ray_options = (
        # --disable-usage-stats in `ray start` saves 10 seconds of idle wait.
        f'--disable-usage-stats '
        f'--port={constants.SKY_REMOTE_RAY_PORT} '
        f'--dashboard-port={constants.SKY_REMOTE_RAY_DASHBOARD_PORT} '
        f'--object-manager-port=8076 '
        f'--temp-dir={constants.SKY_REMOTE_RAY_TEMPDIR}')
    if custom_resource:
        ray_options += f' --resources=\'{custom_resource}\''
        ray_options += _ray_gpu_options(custom_resource)

    if cluster_info.custom_ray_options:
        if 'use_external_ip' in cluster_info.custom_ray_options:
            cluster_info.custom_ray_options.pop('use_external_ip')
        for key, value in cluster_info.custom_ray_options.items():
            ray_options += f' --{key}={value}'

    # Unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY to avoid using credentials
    # from environment variables set by user. SkyPilot's ray cluster should use
    # the `~/.aws/` credentials, as that is the one used to create the cluster,
    # and the autoscaler module started by the `ray start` command should use
    # the same credentials. Otherwise, `ray status` will fail to fetch the
    # available nodes.
    # Reference: https://github.com/skypilot-org/skypilot/issues/2441
    cmd = (f'{constants.SKY_RAY_CMD} stop; '
           'unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; '
           'RAY_SCHEDULER_EVENTS=0 RAY_DEDUP_LOGS=0 '
           f'{constants.SKY_RAY_CMD} start --head {ray_options} || exit 1;' +
           _RAY_PRLIMIT + _DUMP_RAY_PORTS + RAY_HEAD_WAIT_INITIALIZED_COMMAND)
    logger.info(f'Running command on head node: {cmd}')
    # TODO(zhwu): add the output to log files.
    returncode, stdout, stderr = head_runner.run(
        cmd,
        stream_logs=False,
        log_path=log_path_abs,
        require_outputs=True,
        # Source bashrc for starting ray cluster to make sure actors started by
        # ray will have the correct PATH.
        source_bashrc=True)
    if returncode:
        raise RuntimeError('Failed to start ray on the head node '
                           f'(exit code {returncode}). Error: \n'
                           f'===== stdout ===== \n{stdout}\n'
                           f'===== stderr ====={stderr}')


@_log_start_end
@_auto_retry
def start_ray_on_worker_nodes(cluster_name: str, no_restart: bool,
                              custom_resource: Optional[str], ray_port: int,
                              cluster_info: common.ClusterInfo,
                              ssh_credentials: Dict[str, Any]) -> None:
    """Start Ray on the worker nodes."""
    if cluster_info.num_instances <= 1:
        return
    _hint_worker_log_path(cluster_name, cluster_info, 'ray_cluster')
    runners = provision.get_command_runners(cluster_info.provider_name,
                                            cluster_info, **ssh_credentials)
    worker_runners = runners[1:]
    worker_instances = cluster_info.get_worker_instances()
    cache_ids = []
    prev_instance_id = None
    cnt = 0
    for instance in worker_instances:
        if instance.instance_id != prev_instance_id:
            cnt = 0
            prev_instance_id = instance.instance_id
        cache_ids.append(f'{prev_instance_id}-{cnt}')
        cnt += 1

    head_instance = cluster_info.get_head_instance()
    assert head_instance is not None, cluster_info
    use_external_ip = False
    if cluster_info.custom_ray_options:
        # Some cloud providers, e.g. fluidstack, cannot connect to the internal
        # IP of the head node from the worker nodes. In this case, we need to
        # use the external IP of the head node.
        use_external_ip = cluster_info.custom_ray_options.pop(
            'use_external_ip', False)
    head_ip = (head_instance.internal_ip
               if not use_external_ip else head_instance.external_ip)

    ray_options = (f'--address={head_ip}:{constants.SKY_REMOTE_RAY_PORT} '
                   f'--object-manager-port=8076')

    if custom_resource:
        ray_options += f' --resources=\'{custom_resource}\''
        ray_options += _ray_gpu_options(custom_resource)

    if cluster_info.custom_ray_options:
        for key, value in cluster_info.custom_ray_options.items():
            ray_options += f' --{key}={value}'

    # Unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY, see the comment in
    # `start_ray_on_head_node`.
    cmd = (
        f'unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; '
        'RAY_SCHEDULER_EVENTS=0 RAY_DEDUP_LOGS=0 '
        f'{constants.SKY_RAY_CMD} start --disable-usage-stats {ray_options} || '
        'exit 1;' + _RAY_PRLIMIT)
    if no_restart:
        # We do not use ray status to check whether ray is running, because
        # on worker node, if the user started their own ray cluster, ray status
        # will return 0, i.e., we don't know skypilot's ray cluster is running.
        # Instead, we check whether the raylet process is running on gcs address
        # that is connected to the head with the correct port.
        cmd = (f'RAY_PORT={ray_port}; ps aux | grep "ray/raylet/raylet" | '
               f'grep "gcs-address={head_ip}:${{RAY_PORT}}" || '
               f'{{ {cmd} }}')
    else:
        cmd = f'{constants.SKY_RAY_CMD} stop; ' + cmd

    logger.info(f'Running command on worker nodes: {cmd}')

    def _setup_ray_worker(runner_and_id: Tuple[command_runner.CommandRunner,
                                               str]):
        # for cmd in config_from_yaml['worker_start_ray_commands']:
        #     cmd = cmd.replace('$RAY_HEAD_IP', ip_list[0][0])
        #     runner.run(cmd)
        runner, instance_id = runner_and_id
        log_dir = metadata_utils.get_instance_log_dir(cluster_name, instance_id)
        log_path_abs = str(log_dir / ('ray_cluster' + '.log'))
        return runner.run(
            cmd,
            stream_logs=False,
            require_outputs=True,
            log_path=log_path_abs,
            # Source bashrc for starting ray cluster to make sure actors started
            # by ray will have the correct PATH.
            source_bashrc=True)

    results = subprocess_utils.run_in_parallel(
        _setup_ray_worker, list(zip(worker_runners, cache_ids)))
    for returncode, stdout, stderr in results:
        if returncode:
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('Failed to start ray on the worker node '
                                   f'(exit code {returncode}). \n'
                                   'Detailed Error: \n'
                                   f'===== stdout ===== \n{stdout}\n'
                                   f'===== stderr ====={stderr}')


@_log_start_end
@_auto_retry
def start_skylet_on_head_node(cluster_name: str,
                              cluster_info: common.ClusterInfo,
                              ssh_credentials: Dict[str, Any]) -> None:
    """Start skylet on the head node."""
    del cluster_name
    runners = provision.get_command_runners(cluster_info.provider_name,
                                            cluster_info, **ssh_credentials)
    head_runner = runners[0]
    assert cluster_info.head_instance_id is not None, cluster_info
    log_path_abs = str(provision_logging.get_log_path())
    logger.info(f'Running command on head node: {MAYBE_SKYLET_RESTART_CMD}')
    # We need to source bashrc for skylet to make sure the autostop event can
    # access the path to the cloud CLIs.
    returncode, stdout, stderr = head_runner.run(MAYBE_SKYLET_RESTART_CMD,
                                                 stream_logs=False,
                                                 require_outputs=True,
                                                 log_path=log_path_abs,
                                                 source_bashrc=True)
    if returncode:
        raise RuntimeError('Failed to start skylet on the head node '
                           f'(exit code {returncode}). Error: '
                           f'===== stdout ===== \n{stdout}\n'
                           f'===== stderr ====={stderr}')


@_auto_retry
def _internal_file_mounts(file_mounts: Dict,
                          runner: command_runner.CommandRunner,
                          log_path: str) -> None:
    if file_mounts is None or not file_mounts:
        return

    for dst, src in file_mounts.items():
        # TODO: We should use this trick to speed up file mounting:
        # https://stackoverflow.com/questions/1636889/how-can-i-configure-rsync-to-create-target-directory-on-remote-server
        full_src = os.path.abspath(os.path.expanduser(src))

        if os.path.isfile(full_src):
            mkdir_command = f'mkdir -p {os.path.dirname(dst)}'
        else:
            mkdir_command = f'mkdir -p {dst}'

        rc, stdout, stderr = runner.run(mkdir_command,
                                        log_path=log_path,
                                        stream_logs=False,
                                        require_outputs=True)
        subprocess_utils.handle_returncode(
            rc,
            mkdir_command, ('Failed to run command before rsync '
                            f'{src} -> {dst}.'),
            stderr=stdout + stderr)

        runner.rsync(
            source=src,
            target=dst,
            up=True,
            log_path=log_path,
            stream_logs=False,
        )


def _max_workers_for_file_mounts(common_file_mounts: Dict[str, str]) -> int:
    fd_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)

    fd_per_rsync = 5
    for src in common_file_mounts.values():
        if os.path.isdir(src):
            # Assume that each file/folder under src takes 5 file descriptors
            # on average.
            fd_per_rsync = max(fd_per_rsync, len(os.listdir(src)) * 5)

    # Reserve some file descriptors for the system and other processes
    fd_reserve = 100

    max_workers = (fd_limit - fd_reserve) // fd_per_rsync
    # At least 1 worker, and avoid too many workers overloading the system.
    max_workers = min(max(max_workers, 1),
                      subprocess_utils.get_parallel_threads())
    logger.debug(f'Using {max_workers} workers for file mounts.')
    return max_workers


@_log_start_end
def internal_file_mounts(cluster_name: str, common_file_mounts: Dict[str, str],
                         cluster_info: common.ClusterInfo,
                         ssh_credentials: Dict[str, str]) -> None:
    """Executes file mounts - rsyncing internal local files"""
    _hint_worker_log_path(cluster_name, cluster_info, 'internal_file_mounts')

    def _setup_node(runner: command_runner.CommandRunner, log_path: str):
        _internal_file_mounts(common_file_mounts, runner, log_path)

    _parallel_ssh_with_cache(
        _setup_node,
        cluster_name,
        stage_name='internal_file_mounts',
        # Do not cache the file mounts, as the cloud
        # credentials may change, and we should always
        # update the remote files. The internal file_mounts
        # is minimal and should not take too much time.
        digest=None,
        cluster_info=cluster_info,
        ssh_credentials=ssh_credentials,
        max_workers=_max_workers_for_file_mounts(common_file_mounts))
