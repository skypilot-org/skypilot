"""Setup dependencies & services for instances."""
import os
import time
from typing import List, Dict, Optional
import hashlib
from concurrent import futures

from sky import sky_logging
from sky.utils import command_runner, subprocess_utils, common_utils, ux_utils
from sky.provision import common
from sky.provision import metadata_utils

logger = sky_logging.init_logger(__name__)

_MAX_RETRY = 5

# Increase the limit of the number of open files for the raylet process,
# as the `ulimit` may not take effect at this point, because it requires
_RAY_PRLIMIT = (
    'which prlimit && for id in $(pgrep -f raylet/raylet); '
    'do sudo prlimit --nofile=1048576:1048576 --pid=$id || true; done;')


def _auto_retry(func):

    def retry(*args, **kwargs):
        backoff = common_utils.Backoff(initial_backoff=1, max_backoff_factor=5)
        for retry_cnt in range(_MAX_RETRY):
            try:
                return func(*args, **kwargs)
            except Exception as e:  # pylint: disable=broad-except
                if retry_cnt >= _MAX_RETRY - 1:
                    raise e
                sleep = backoff.current_backoff()
                logger.info('Retrying in {:.1f} seconds.'.format(sleep))
                time.sleep(sleep)

    return retry


def _parallel_ssh_with_cache(func, cluster_name: str, stage_name: str,
                             digest: str,
                             cluster_metadata: common.ClusterMetadata,
                             ssh_credentials: Dict[str, str]) -> None:
    with futures.ThreadPoolExecutor(max_workers=32) as pool:
        results = []
        for instance_id, metadata in cluster_metadata.instances.items():
            runner = command_runner.SSHCommandRunner(metadata.get_feasible_ip(),
                                                     **ssh_credentials)
            wrapper = metadata_utils.cache_func(cluster_name, instance_id,
                                                stage_name, digest)
            log_dir_abs = metadata_utils.get_instance_log_dir(
                cluster_name, instance_id)
            log_path_abs = str(log_dir_abs / (stage_name + '.log'))
            results.append(
                pool.submit(wrapper(func), runner, metadata, log_path_abs))

        for future in results:
            future.result()


def internal_dependencies_setup(cluster_name: str, setup_commands: List[str],
                                cluster_metadata: common.ClusterMetadata,
                                ssh_credentials: Dict[str, str]) -> None:
    """Setup internal dependencies."""
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
                    metadata: common.InstanceMetadata, log_path: str):
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
                             stage_name='internal_dependencies_setup',
                             digest=digest,
                             cluster_metadata=cluster_metadata,
                             ssh_credentials=ssh_credentials)


@_auto_retry
def start_ray_head_node(ssh_runner: command_runner.SSHCommandRunner,
                        custom_resource: Optional[str]) -> None:
    """Start Ray on the head node."""
    ray_options = '--port=6379 --object-manager-port=8076'
    if custom_resource:
        ray_options += f' --resources=\'{custom_resource}\''

    returncode, stdout, stderr = ssh_runner.run(
        'ray stop; ray start --disable-usage-stats --head '
        f'{ray_options};' + _RAY_PRLIMIT,
        stream_logs=False,
        require_outputs=True)
    if returncode:
        raise RuntimeError('Failed to start ray on the head node '
                           f'(exit code {returncode}). Error: '
                           f'===== stdout ===== \n{stdout}\n'
                           f'===== stderr ====={stderr}')


@_auto_retry
def start_ray_worker_nodes(ssh_runners: List[command_runner.SSHCommandRunner],
                           head_private_ip: str, no_restart: bool,
                           custom_resource: Optional[str]) -> None:
    """Start Ray on the worker nodes."""
    if not ssh_runners:
        return

    ray_options = f'--address={head_private_ip}:6379'
    if custom_resource:
        ray_options += f' --resources=\'{custom_resource}\''

    cmd = f'ray start --disable-usage-stats {ray_options};' + _RAY_PRLIMIT
    if no_restart:
        cmd = 'ray status || ' + cmd
    else:
        cmd = 'ray stop; ' + cmd

    def _setup_ray_worker(runner: command_runner.SSHCommandRunner):
        # for cmd in config_from_yaml['worker_start_ray_commands']:
        #     cmd = cmd.replace('$RAY_HEAD_IP', ip_list[0][0])
        #     runner.run(cmd)
        return runner.run(cmd, stream_logs=False, require_outputs=True)

    results = subprocess_utils.run_in_parallel(_setup_ray_worker, ssh_runners)
    for returncode, stdout, stderr in results:
        if returncode:
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('Failed to start ray on the worker node '
                                   f'(exit code {returncode}). Error: '
                                   f'===== stdout ===== \n{stdout}\n'
                                   f'===== stderr ====={stderr}')


@_auto_retry
def start_skylet(ssh_runner: command_runner.SSHCommandRunner) -> None:
    """Start skylet on the header node."""
    # "source ~/.bashrc" has side effects similar to
    # https://stackoverflow.com/questions/29709790/scripts-with-nohup-inside-dont-exit-correctly
    # This side effects blocks SSH from exiting. We address it by nesting
    # bash commands.
    returncode, stdout, stderr = ssh_runner.run(
        '(ps aux | grep -v nohup | grep -v grep | grep -q '
        '-- "python3 -m sky.skylet.skylet") || (bash -c \'source ~/.bashrc '
        '&& nohup python3 -m sky.skylet.skylet >> ~/.sky/skylet.log 2>&1 &\' '
        '&> /dev/null &)',
        stream_logs=False,
        require_outputs=True)
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


def internal_file_mounts(cluster_name: str, common_file_mounts: Dict,
                         cluster_metadata: common.ClusterMetadata,
                         ssh_credentials: Dict[str,
                                               str], wheel_hash: str) -> None:
    """Executes file mounts - rsyncing internal local files"""

    def _setup_node(runner: command_runner.SSHCommandRunner,
                    metadata: common.InstanceMetadata, log_path: str):
        del metadata
        _internal_file_mounts(common_file_mounts, runner, log_path)

    _parallel_ssh_with_cache(_setup_node,
                             cluster_name,
                             stage_name='internal_file_mounts',
                             digest=wheel_hash,
                             cluster_metadata=cluster_metadata,
                             ssh_credentials=ssh_credentials)
