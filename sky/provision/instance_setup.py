"""Setup dependencies & services for instances."""
from concurrent import futures
import functools
import hashlib
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from sky import exceptions
from sky import provision
from sky import sky_logging
from sky.provision import common
from sky.provision import docker_utils
from sky.provision import logging as provision_logging
from sky.provision import metadata_utils
from sky.skylet import constants
from sky.usage import constants as usage_constants
from sky.usage import usage_lib
from sky.utils import accelerator_registry
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import env_options
from sky.utils import subprocess_utils
from sky.utils import timeline
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

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
    '"from sky import sky_logging\n'
    'with sky_logging.silent(): '
    'from sky.skylet import job_lib; print(job_lib.get_ray_port())" '
    '2> /dev/null || echo 6379);'
    f'{constants.SKY_PYTHON_CMD} -c "from sky.utils import message_utils; '
    'print(message_utils.encode_payload({\'ray_port\': $RAY_PORT}))"')

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
# We need to activate the python environment to make sure autostop in skylet
# can find the cloud SDK/CLI in PATH.
MAYBE_SKYLET_RESTART_CMD = (f'{constants.ACTIVATE_SKY_REMOTE_PYTHON_ENV}; '
                            f'{constants.SKY_PYTHON_CMD} -m '
                            'sky.skylet.attempt_skylet;')


def _set_usage_run_id_cmd() -> str:
    """Gets the command to set the usage run id.

    The command saves the current usage run id to the file, so that the skylet
    can use it to report the heartbeat.

    We use a function instead of a constant so that the usage run id is the
    latest one when the function is called.
    """
    return (
        f'cat {usage_constants.USAGE_RUN_ID_FILE} || '
        # The run id is retrieved locally for the current run, so that the
        # remote cluster will be set with the same run id as the initial
        # launch operation.
        f'echo "{usage_lib.messages.usage.run_id}" > '
        f'{usage_constants.USAGE_RUN_ID_FILE}')


def _set_skypilot_env_var_cmd() -> str:
    """Sets the skypilot environment variables on the remote machine."""
    env_vars = env_options.Options.all_options()
    return '; '.join([f'export {k}={v}' for k, v in env_vars.items()])


def _auto_retry(should_retry: Callable[[Exception], bool] = lambda _: True):
    """Decorator that retries the function if it fails.

    This decorator is mostly for SSH disconnection issues, which might happen
    during the setup of instances.
    """

    def decorator(func):

        @functools.wraps(func)
        def retry(*args, **kwargs):
            backoff = common_utils.Backoff(initial_backoff=1,
                                           max_backoff_factor=5)
            for retry_cnt in range(_MAX_RETRY):
                try:
                    return func(*args, **kwargs)
                except Exception as e:  # pylint: disable=broad-except
                    if not should_retry(e) or retry_cnt >= _MAX_RETRY - 1:
                        raise
                    sleep = backoff.current_backoff()
                    logger.info(
                        f'{func.__name__}: Retrying in {sleep:.1f} seconds, '
                        f'due to {e}')
                    time.sleep(sleep)

        return retry

    return decorator


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
        max_workers = subprocess_utils.get_parallel_threads(
            cluster_info.provider_name)
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


@common.log_function_start_end
def initialize_docker(cluster_name: str, docker_config: Dict[str, Any],
                      cluster_info: common.ClusterInfo,
                      ssh_credentials: Dict[str, Any]) -> Optional[str]:
    """Setup docker on the cluster."""
    if not docker_config:
        return None
    _hint_worker_log_path(cluster_name, cluster_info, 'initialize_docker')

    @_auto_retry(should_retry=lambda e: isinstance(e, exceptions.CommandError)
                 and e.returncode == 255)
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


@common.log_function_start_end
@timeline.event
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

    @_auto_retry()
    def _setup_node(runner: command_runner.CommandRunner, log_path: str):
        for cmd in setup_commands:
            returncode, stdout, stderr = runner.run(
                cmd,
                stream_logs=False,
                log_path=log_path,
                require_outputs=True,
                # Installing dependencies requires source bashrc to access
                # conda.
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


def ray_head_start_command(custom_resource: Optional[str],
                           custom_ray_options: Optional[Dict[str, Any]]) -> str:
    """Returns the command to start Ray on the head node."""
    ray_options = (
        # --disable-usage-stats in `ray start` saves 10 seconds of idle wait.
        f'--disable-usage-stats '
        f'--port={constants.SKY_REMOTE_RAY_PORT} '
        f'--dashboard-port={constants.SKY_REMOTE_RAY_DASHBOARD_PORT} '
        f'--min-worker-port 11002 '
        f'--object-manager-port=8076 '
        f'--temp-dir={constants.SKY_REMOTE_RAY_TEMPDIR}')
    if custom_resource:
        ray_options += f' --resources=\'{custom_resource}\''
        ray_options += _ray_gpu_options(custom_resource)
    if custom_ray_options:
        if 'use_external_ip' in custom_ray_options:
            custom_ray_options.pop('use_external_ip')
        for key, value in custom_ray_options.items():
            ray_options += f' --{key}={value}'

    cmd = (
        f'{constants.SKY_RAY_CMD} stop; '
        'RAY_SCHEDULER_EVENTS=0 RAY_DEDUP_LOGS=0 '
        # worker_maximum_startup_concurrency controls the maximum number of
        # workers that can be started concurrently. However, it also controls
        # this warning message:
        # https://github.com/ray-project/ray/blob/d5d03e6e24ae3cfafb87637ade795fb1480636e6/src/ray/raylet/worker_pool.cc#L1535-L1545
        # maximum_startup_concurrency defaults to the number of CPUs given by
        # multiprocessing.cpu_count() or manually specified to ray. (See
        # https://github.com/ray-project/ray/blob/fab26e1813779eb568acba01281c6dd963c13635/python/ray/_private/services.py#L1622-L1624.)
        # The warning will show when the number of workers is >4x the
        # maximum_startup_concurrency, so typically 4x CPU count. However, the
        # job controller uses 0.25cpu reservations, and each job can use two
        # workers (one for the submitted job and one for remote actors),
        # resulting in a worker count of 8x CPUs or more. Increase the
        # worker_maximum_startup_concurrency to 3x CPUs so that we will only see
        # the warning when the worker count is >12x CPUs.
        'RAY_worker_maximum_startup_concurrency=$(( 3 * $(nproc --all) )) '
        f'{constants.SKY_RAY_CMD} start --head {ray_options} || exit 1;' +
        _RAY_PRLIMIT + _DUMP_RAY_PORTS + RAY_HEAD_WAIT_INITIALIZED_COMMAND)
    return cmd


def ray_worker_start_command(custom_resource: Optional[str],
                             custom_ray_options: Optional[Dict[str, Any]],
                             no_restart: bool) -> str:
    """Returns the command to start Ray on the worker node."""
    # We need to use the ray port in the env variable, because the head node
    # determines the port to be used for the worker node.
    ray_options = ('--address=${SKYPILOT_RAY_HEAD_IP}:${SKYPILOT_RAY_PORT} '
                   '--object-manager-port=8076')

    if custom_resource:
        ray_options += f' --resources=\'{custom_resource}\''
        ray_options += _ray_gpu_options(custom_resource)

    if custom_ray_options:
        for key, value in custom_ray_options.items():
            ray_options += f' --{key}={value}'

    cmd = (
        'RAY_SCHEDULER_EVENTS=0 RAY_DEDUP_LOGS=0 '
        f'{constants.SKY_RAY_CMD} start --disable-usage-stats {ray_options} || '
        'exit 1;' + _RAY_PRLIMIT)
    if no_restart:
        # We do not use ray status to check whether ray is running, because
        # on worker node, if the user started their own ray cluster, ray status
        # will return 0, i.e., we don't know skypilot's ray cluster is running.
        # Instead, we check whether the raylet process is running on gcs address
        # that is connected to the head with the correct port.
        cmd = (
            f'ps aux | grep "ray/raylet/raylet" | '
            'grep "gcs-address=${SKYPILOT_RAY_HEAD_IP}:${SKYPILOT_RAY_PORT}" '
            f'|| {{ {cmd} }}')
    else:
        cmd = f'{constants.SKY_RAY_CMD} stop; ' + cmd
    return cmd


@common.log_function_start_end
@_auto_retry()
@timeline.event
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
    cmd = ray_head_start_command(custom_resource,
                                 cluster_info.custom_ray_options)
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


@common.log_function_start_end
@_auto_retry()
@timeline.event
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

    ray_cmd = ray_worker_start_command(custom_resource,
                                       cluster_info.custom_ray_options,
                                       no_restart)

    cmd = (f'export SKYPILOT_RAY_HEAD_IP="{head_ip}"; '
           f'export SKYPILOT_RAY_PORT={ray_port}; ' + ray_cmd)

    logger.info(f'Running command on worker nodes: {cmd}')

    def _setup_ray_worker(runner_and_id: Tuple[command_runner.CommandRunner,
                                               str]):
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

    num_threads = subprocess_utils.get_parallel_threads(
        cluster_info.provider_name)
    results = subprocess_utils.run_in_parallel(
        _setup_ray_worker, list(zip(worker_runners, cache_ids)), num_threads)
    for returncode, stdout, stderr in results:
        if returncode:
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('Failed to start ray on the worker node '
                                   f'(exit code {returncode}). \n'
                                   'Detailed Error: \n'
                                   f'===== stdout ===== \n{stdout}\n'
                                   f'===== stderr ====={stderr}')


@common.log_function_start_end
@_auto_retry()
@timeline.event
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
    set_usage_run_id_cmd = _set_usage_run_id_cmd()
    # Set the skypilot environment variables, including the usage type, debug
    # info, and other options.
    set_skypilot_env_var_cmd = _set_skypilot_env_var_cmd()
    returncode, stdout, stderr = head_runner.run(
        f'{set_usage_run_id_cmd}; {set_skypilot_env_var_cmd}; '
        f'{MAYBE_SKYLET_RESTART_CMD}',
        stream_logs=False,
        require_outputs=True,
        log_path=log_path_abs,
        source_bashrc=True)
    if returncode:
        raise RuntimeError('Failed to start skylet on the head node '
                           f'(exit code {returncode}). Error: '
                           f'===== stdout ===== \n{stdout}\n'
                           f'===== stderr ====={stderr}')


@_auto_retry()
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


@common.log_function_start_end
@timeline.event
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
        max_workers=subprocess_utils.get_max_workers_for_file_mounts(
            common_file_mounts, cluster_info.provider_name))
