"""Setup dependencies & services for instances."""
from concurrent import futures
import functools
import hashlib
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.provision import common
from sky.provision import docker_utils
from sky.provision import logging as provision_logging
from sky.provision import metadata_utils
from sky.skylet import constants
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)
_START_TITLE = '\n' + '-' * 20 + 'Start: {} ' + '-' * 20
_END_TITLE = '-' * 20 + 'End:   {} ' + '-' * 20 + '\n'

_MAX_RETRY = 5

# Increase the limit of the number of open files for the raylet process,
# as the `ulimit` may not take effect at this point, because it requires
# all the sessions to be reloaded. This is a workaround.
_RAY_PRLIMIT = (
    'which prlimit && for id in $(pgrep -f raylet/raylet); '
    'do sudo prlimit --nofile=1048576:1048576 --pid=$id || true; done;')

_DUMP_RAY_PORTS = (
    'python -c \'import json, os; '
    f'json.dump({constants.SKY_REMOTE_RAY_PORT_DICT_STR}, '
    f'open(os.path.expanduser("{constants.SKY_REMOTE_RAY_PORT_FILE}"), "w"))\'')

_RAY_PORT_COMMAND = (
    'RAY_PORT=$(python -c "from sky.skylet import job_lib; '
    'print(job_lib.get_ray_port())" 2> /dev/null || echo 6379)')

# Command that calls `ray status` with SkyPilot's Ray port set.
RAY_STATUS_WITH_SKY_RAY_PORT_COMMAND = (
    f'{_RAY_PORT_COMMAND}; '
    'RAY_ADDRESS=127.0.0.1:$RAY_PORT ray status')

# Restart skylet when the version does not match to keep the skylet up-to-date.
_MAYBE_SKYLET_RESTART_CMD = 'python3 -m sky.skylet.attempt_skylet'


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
                logger.info(f'Retrying in {sleep:.1f} seconds.')
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
    if len(cluster_info.instances) > 1:
        worker_log_path = metadata_utils.get_instance_log_dir(
            cluster_name, '*') / (stage_name + '.log')
        logger.info(f'Logs of worker nodes can be found at: {worker_log_path}')


def _parallel_ssh_with_cache(func, cluster_name: str, stage_name: str,
                             digest: Optional[str],
                             cluster_info: common.ClusterInfo,
                             ssh_credentials: Dict[str, Any]) -> List[Any]:
    with futures.ThreadPoolExecutor(max_workers=32) as pool:
        results = []
        for instance_id, metadata in cluster_info.instances.items():
            runner = command_runner.SSHCommandRunner(metadata.get_feasible_ip(),
                                                     port=22,
                                                     **ssh_credentials)
            wrapper = metadata_utils.cache_func(cluster_name, instance_id,
                                                stage_name, digest)
            if cluster_info.head_instance_id == instance_id:
                # Log the head node's output to the provision.log
                log_path_abs = str(provision_logging.get_log_path())
            else:
                log_dir_abs = metadata_utils.get_instance_log_dir(
                    cluster_name, instance_id)
                log_path_abs = str(log_dir_abs / (stage_name + '.log'))
            results.append(
                pool.submit(wrapper(func), runner, metadata, log_path_abs))

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
    def _initialize_docker(runner: command_runner.SSHCommandRunner,
                           metadata: common.InstanceInfo, log_path: str):
        del metadata  # Unused.
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
    def _setup_node(runner: command_runner.SSHCommandRunner,
                    metadata: common.InstanceInfo, log_path: str):
        del metadata
        for cmd in setup_commands:
            returncode, stdout, stderr = runner.run(cmd,
                                                    stream_logs=False,
                                                    log_path=log_path,
                                                    require_outputs=True)
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


@_log_start_end
@_auto_retry
def start_ray_on_head_node(cluster_name: str, custom_resource: Optional[str],
                           cluster_info: common.ClusterInfo,
                           ssh_credentials: Dict[str, Any]) -> None:
    """Start Ray on the head node."""
    ip_list = cluster_info.get_feasible_ips()
    ssh_runner = command_runner.SSHCommandRunner(ip_list[0],
                                                 port=22,
                                                 **ssh_credentials)
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

    # Unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY to avoid using credentials
    # from environment variables set by user. SkyPilot's ray cluster should use
    # the `~/.aws/` credentials, as that is the one used to create the cluster,
    # and the autoscaler module started by the `ray start` command should use
    # the same credentials. Otherwise, `ray status` will fail to fetch the
    # available nodes.
    # Reference: https://github.com/skypilot-org/skypilot/issues/2441
    cmd = ('ray stop; unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; '
           'RAY_SCHEDULER_EVENTS=0 RAY_DEDUP_LOGS=0 '
           f'ray start --head {ray_options} || exit 1;' + _RAY_PRLIMIT +
           _DUMP_RAY_PORTS)
    logger.info(f'Running command on head node: {cmd}')
    # TODO(zhwu): add the output to log files.
    returncode, stdout, stderr = ssh_runner.run(cmd,
                                                stream_logs=False,
                                                log_path=log_path_abs,
                                                require_outputs=True)
    if returncode:
        raise RuntimeError('Failed to start ray on the head node '
                           f'(exit code {returncode}). Error: '
                           f'===== stdout ===== \n{stdout}\n'
                           f'===== stderr ====={stderr}')


@_log_start_end
@_auto_retry
def start_ray_on_worker_nodes(cluster_name: str, no_restart: bool,
                              custom_resource: Optional[str],
                              cluster_info: common.ClusterInfo,
                              ssh_credentials: Dict[str, Any]) -> None:
    """Start Ray on the worker nodes."""
    if len(cluster_info.instances) <= 1:
        return
    _hint_worker_log_path(cluster_name, cluster_info, 'ray_cluster')
    ip_list = cluster_info.get_feasible_ips()
    ssh_runners = command_runner.SSHCommandRunner.make_runner_list(
        ip_list[1:], port_list=None, **ssh_credentials)
    worker_ids = [
        instance_id for instance_id in cluster_info.instances
        if instance_id != cluster_info.head_instance_id
    ]
    head_instance = cluster_info.get_head_instance()
    assert head_instance is not None, cluster_info
    head_private_ip = head_instance.internal_ip

    ray_options = (
        f'--address={head_private_ip}:{constants.SKY_REMOTE_RAY_PORT} '
        f'--object-manager-port=8076')
    if custom_resource:
        ray_options += f' --resources=\'{custom_resource}\''

    # Unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY, see the comment in
    # `start_ray_on_head_node`.
    cmd = (f'unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; '
           'RAY_SCHEDULER_EVENTS=0 RAY_DEDUP_LOGS=0 '
           f'ray start --disable-usage-stats {ray_options} || exit 1;' +
           _RAY_PRLIMIT + _DUMP_RAY_PORTS)
    if no_restart:
        # We do not use ray status to check whether ray is running, because
        # on worker node, if the user started their own ray cluster, ray status
        # will return 0, i.e., we don't know skypilot's ray cluster is running.
        # Instead, we check whether the raylet process is running on gcs address
        # that is connected to the head with the correct port.
        cmd = (f'{_RAY_PORT_COMMAND}; ps aux | grep "ray/raylet/raylet" | '
               f'grep "gcs-address={head_private_ip}:${{RAY_PORT}}" || '
               f'{{ {cmd}; }}')
    else:
        cmd = 'ray stop; ' + cmd

    logger.info(f'Running command on worker nodes: {cmd}')

    def _setup_ray_worker(runner_and_id: Tuple[command_runner.SSHCommandRunner,
                                               str]):
        # for cmd in config_from_yaml['worker_start_ray_commands']:
        #     cmd = cmd.replace('$RAY_HEAD_IP', ip_list[0][0])
        #     runner.run(cmd)
        runner, instance_id = runner_and_id
        log_dir = metadata_utils.get_instance_log_dir(cluster_name, instance_id)
        log_path_abs = str(log_dir / ('ray_cluster' + '.log'))
        return runner.run(cmd,
                          stream_logs=False,
                          require_outputs=True,
                          log_path=log_path_abs)

    results = subprocess_utils.run_in_parallel(
        _setup_ray_worker, list(zip(ssh_runners, worker_ids)))
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
    ip_list = cluster_info.get_feasible_ips()
    ssh_runner = command_runner.SSHCommandRunner(ip_list[0],
                                                 port=22,
                                                 **ssh_credentials)
    assert cluster_info.head_instance_id is not None, cluster_info
    log_path_abs = str(provision_logging.get_log_path())
    logger.info(f'Running command on head node: {_MAYBE_SKYLET_RESTART_CMD}')
    returncode, stdout, stderr = ssh_runner.run(_MAYBE_SKYLET_RESTART_CMD,
                                                stream_logs=False,
                                                require_outputs=True,
                                                log_path=log_path_abs)
    if returncode:
        raise RuntimeError('Failed to start skylet on the head node '
                           f'(exit code {returncode}). Error: '
                           f'===== stdout ===== \n{stdout}\n'
                           f'===== stderr ====={stderr}')


@_auto_retry
def _internal_file_mounts(file_mounts: Dict,
                          runner: command_runner.SSHCommandRunner,
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


@_log_start_end
def internal_file_mounts(cluster_name: str, common_file_mounts: Dict,
                         cluster_info: common.ClusterInfo,
                         ssh_credentials: Dict[str, str]) -> None:
    """Executes file mounts - rsyncing internal local files"""
    _hint_worker_log_path(cluster_name, cluster_info, 'internal_file_mounts')

    def _setup_node(runner: command_runner.SSHCommandRunner,
                    metadata: common.InstanceInfo, log_path: str):
        del metadata
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
        ssh_credentials=ssh_credentials)
