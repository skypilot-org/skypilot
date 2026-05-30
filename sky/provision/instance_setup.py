"""Setup dependencies & services for instances."""
import base64
from concurrent import futures
import functools
import gzip
import hashlib
import json
import os
import shlex
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from sky import exceptions
from sky import logs
from sky import provision
from sky import resources as resources_lib
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
from sky.utils import resources_utils
from sky.utils import source_utils
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

DUMP_RAY_PORTS = (f'{constants.SKY_PYTHON_CMD} -c \'import json, os; '
                  f'runtime_dir = os.path.expanduser(os.environ.get('
                  f'"{constants.SKY_RUNTIME_DIR_ENV_VAR_KEY}", "~")); '
                  f'json.dump({constants.SKY_REMOTE_RAY_PORT_DICT_STR}, '
                  f'open(os.path.join(runtime_dir, '
                  f'"{constants.SKY_REMOTE_RAY_PORT_FILE}"), "w", '
                  'encoding="utf-8"))\';')

_HOST_NETWORK_ENV_FILE = '/tmp/sky_host_network_ports.env'
_HOST_NETWORK_PROBE_TARGET = '/tmp/sky_host_network_probe.py'


@functools.lru_cache(maxsize=1)
def _host_network_probe_b64() -> str:
    # Cached, not module-level: instance_setup is imported on every `import
    # sky`, but the probe payload is only needed when actually building a
    # launch command, so the read/minify/gzip cost stays off the CLI path.
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'kubernetes', 'host_network_probe.py')
    with open(path, 'r', encoding='utf-8') as f:
        source = f.read()
    minified = source_utils.minify_python_source(source)
    # Sanity-check: minified source must still parse.
    compile(minified, path, 'exec')
    compressed = gzip.compress(minified.encode('utf-8'))
    return base64.b64encode(compressed).decode('ascii')


def _host_network_probe_cmd(mode: str) -> str:
    """Bash snippet that probes Ray + sshd ports when running with hostNetwork.

    Returns a runtime-gated snippet: a no-op shell branch unless
    SKYPILOT_HOST_NETWORK=1 and SKYPILOT_RAY_PORTS_CONFIGMAP_NAME are
    set in the pod env. Non-hostNetwork bootstraps see no change — env
    vars stay unset and ``${VAR:-default}`` in the ray flags falls back
    to the constants.

    Also rebinds the pod's sshd to the probed SKYPILOT_SSHD_PORT. Under
    hostNetwork the node's own sshd already owns host:22 so the pod's
    sshd silently failed to bind in apt-ssh-setup; rewriting Port in
    sshd_config and restarting picks up the probed port instead.

    The probe is shipped gzip+base64-inline rather than invoked as a
    module because the K8s template installs stable skypilot from PyPI
    before ray_head_start_command runs (the dev wheel ships later), so
    neither `python -m sky.provision.kubernetes.host_network_probe` nor
    a site-packages file lookup is reliable at probe time. The b64 form
    also keeps the payload on a single line, which is required to stay
    inside the rendered YAML's block-scalar indentation. Gzip cuts the
    payload by ~4x; the source on disk stays readable.
    """
    assert mode in ('head', 'worker'), mode
    return (
        'if [ "${SKYPILOT_HOST_NETWORK:-0}" = "1" ] && '
        '[ -n "${SKYPILOT_RAY_PORTS_CONFIGMAP_NAME:-}" ]; then '
        f'echo \'{_host_network_probe_b64()}\' | base64 -d | gunzip > '
        f'{_HOST_NETWORK_PROBE_TARGET}; '
        f'{constants.SKY_PYTHON_CMD} {_HOST_NETWORK_PROBE_TARGET} '
        f'--mode {mode} '
        f'--env-file {_HOST_NETWORK_ENV_FILE} '
        '--configmap-name "$SKYPILOT_RAY_PORTS_CONFIGMAP_NAME" '
        '--configmap-namespace '
        '"$SKYPILOT_RAY_PORTS_CONFIGMAP_NAMESPACE" || exit 1; '
        f'set -a; . {_HOST_NETWORK_ENV_FILE}; set +a; '
        # Delete-then-append rather than sed-in-place: sshd_config files
        # without an existing Port directive would otherwise keep the
        # default 22 (where the K8s node's own sshd already listens).
        'if [ -n "${SKYPILOT_SSHD_PORT:-}" ]; then '
        'sudo sh -c "'
        'sed -i -E \'/^[[:space:]]*#?[[:space:]]*Port[[:space:]]+/d\' '
        '/etc/ssh/sshd_config && '
        'echo Port ${SKYPILOT_SSHD_PORT} >> /etc/ssh/sshd_config && '
        'service ssh restart"; '
        'fi; '
        'fi; ')


# Stdlib only — deliberately doesn't import `sky` so the read still
# works during the K8s bootstrap's brief stable→dev skypilot reinstall
# window. Falls back to SKY_REMOTE_RAY_PORT (not legacy 6379) so the
# health-check poll lands on something Ray could plausibly be on.
_READ_RAY_PORT_PY = (
    'import json, os; '
    'd = os.path.expanduser(os.environ.get('
    f'\'{constants.SKY_RUNTIME_DIR_ENV_VAR_KEY}\', \'~\')); '
    'print(json.load(open(os.path.join(d, '
    f'\'{constants.SKY_REMOTE_RAY_PORT_FILE}\')))[\'ray_port\'])')
_RAY_PORT_COMMAND = (
    f'RAY_PORT=$({constants.SKY_PYTHON_CMD} -c "{_READ_RAY_PORT_PY}" '
    f'2> /dev/null || echo {constants.SKY_REMOTE_RAY_PORT});'
    f'{constants.SKY_PYTHON_CMD} -c "from sky.utils import message_utils; '
    'print(message_utils.encode_payload({\'ray_port\': $RAY_PORT}))"')

# Command that calls `ray status` with SkyPilot's Ray port set.
RAY_STATUS_WITH_SKY_RAY_PORT_COMMAND = (
    f'{_RAY_PORT_COMMAND}; '
    f'RAY_ADDRESS=127.0.0.1:$RAY_PORT {constants.SKY_RAY_CMD} status')

# Command that waits for the ray status to be initialized. Otherwise, a later
# `sky status -r` may fail due to the ray cluster not being ready.
# Reads SKYPILOT_RAY_PORT (exported by the hostNetwork probe) rather than
# the constants.RAY_STATUS literal — the probe picks a dynamic port.
RAY_HEAD_WAIT_INITIALIZED_COMMAND = (
    'while `RAY_ADDRESS=127.0.0.1:${SKYPILOT_RAY_PORT:-'
    f'{constants.SKY_REMOTE_RAY_PORT}}} '
    f'{constants.SKY_RAY_CMD} status | grep -q "No cluster status."`; do '
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
        f'cat {usage_constants.USAGE_RUN_ID_FILE} 2> /dev/null || '
        # The run id is retrieved locally for the current run, so that the
        # remote cluster will be set with the same run id as the initial
        # launch operation.
        f'echo "{usage_lib.messages.usage.run_id}" > '
        f'{usage_constants.USAGE_RUN_ID_FILE}')


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


class SSHThreadPoolExecutor(futures.ThreadPoolExecutor):
    """ThreadPoolExecutor that kills children processes on exit."""

    def __exit__(self, exc_type, exc_val, exc_tb):
        # ssh command runner eventually calls
        # log_lib.run_with_log, which will spawn
        # subprocesses. If we are exiting the context
        # we need to kill the children processes
        # to avoid leakage.
        subprocess_utils.kill_children_processes()
        self.shutdown()
        return False


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
    with SSHThreadPoolExecutor(max_workers=max_workers) as pool:
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
            returncode, stdout, stderr = runner.run_setup(
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
                returncode, stdout, stderr = runner.run_setup(
                    cmd,
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


# Ray port flags shared by the head and worker start commands. The
# ${VAR:-default} forms take the probed value when the hostNetwork probe
# ran and the original constant otherwise; the ${VAR:+--flag=...} forms
# vanish when unset, so non-hostNetwork bootstraps defer to Ray's own
# defaults. Kept in one place so the head/worker flag sets can't drift.
_SHARED_RAY_PORT_FLAGS = (
    '--object-manager-port=${SKYPILOT_RAY_OBJECT_MANAGER_PORT:-8076} '
    '${SKYPILOT_RAY_NODE_MANAGER_PORT:+'
    '--node-manager-port=$SKYPILOT_RAY_NODE_MANAGER_PORT} '
    '${SKYPILOT_RAY_DASHBOARD_AGENT_LISTEN_PORT:+'
    '--dashboard-agent-listen-port='
    '$SKYPILOT_RAY_DASHBOARD_AGENT_LISTEN_PORT} '
    '${SKYPILOT_RAY_RUNTIME_ENV_AGENT_PORT:+'
    '--runtime-env-agent-port=$SKYPILOT_RAY_RUNTIME_ENV_AGENT_PORT} '
    '${SKYPILOT_RAY_METRICS_EXPORT_PORT:+'
    '--metrics-export-port=$SKYPILOT_RAY_METRICS_EXPORT_PORT}')


def ray_head_start_command(custom_resource: Optional[str],
                           custom_ray_options: Optional[Dict[str, Any]]) -> str:
    """Returns the command to start Ray on the head node."""
    ray_options = (
        # --disable-usage-stats in `ray start` saves 10 seconds of idle wait.
        f'--disable-usage-stats '
        f'--port=${{SKYPILOT_RAY_PORT:-{constants.SKY_REMOTE_RAY_PORT}}} '
        f'--dashboard-port=${{SKYPILOT_RAY_DASHBOARD_PORT:-'
        f'{constants.SKY_REMOTE_RAY_DASHBOARD_PORT}}} '
        f'--min-worker-port 11002 '
        f'{_SHARED_RAY_PORT_FLAGS} '
        # Head-only: workers don't run the Ray Client server.
        '${SKYPILOT_RAY_CLIENT_SERVER_PORT:+'
        '--ray-client-server-port=$SKYPILOT_RAY_CLIENT_SERVER_PORT} '
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
        _host_network_probe_cmd('head') + f'{constants.SKY_RAY_CMD} stop; '
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
        _RAY_PRLIMIT + DUMP_RAY_PORTS + RAY_HEAD_WAIT_INITIALIZED_COMMAND)
    return cmd


def ray_worker_start_command(custom_resource: Optional[str],
                             custom_ray_options: Optional[Dict[str, Any]],
                             no_restart: bool) -> str:
    """Returns the command to start Ray on the worker node."""
    # SKYPILOT_RAY_PORT is the head's GCS port — exported as a constant
    # by the K8s template's worker bootstrap, or as the probed value
    # (read from the head's ConfigMap) by the hostNetwork worker probe.
    ray_options = ('--address=${SKYPILOT_RAY_HEAD_IP}:${SKYPILOT_RAY_PORT} '
                   f'{_SHARED_RAY_PORT_FLAGS}')

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
    return _host_network_probe_cmd('worker') + cmd


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

    if use_external_ip:
        head_ip = head_instance.external_ip
    else:
        # For Kubernetes, use the internal service address of the head node.
        # Keep this consistent with the logic in kubernetes-ray.yml.j2
        if head_instance.internal_svc:
            head_ip = head_instance.internal_svc
        else:
            head_ip = head_instance.internal_ip

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
def start_skylet_on_head_node(
        cluster_name: resources_utils.ClusterName,
        cluster_info: common.ClusterInfo, ssh_credentials: Dict[str, Any],
        launched_resources: resources_lib.Resources) -> None:
    """Start skylet on the head node."""
    # Avoid circular import.
    # pylint: disable=import-outside-toplevel
    from sky.utils import controller_utils

    def _set_skypilot_env_var_cmd() -> str:
        """Sets the skypilot environment variables on the remote machine."""
        env_vars = {
            k: str(v) for (k, v) in env_options.Options.all_options().items()
        }
        is_controller = controller_utils.Controllers.from_name(
            cluster_name.display_name) is not None
        is_kubernetes = cluster_info.provider_name == 'kubernetes'
        if is_controller and is_kubernetes:
            # For jobs/serve controller, we pass in the CPU and memory limits
            # when starting the skylet to handle cases where these env vars
            # are not set on the cluster's pod spec. The skylet will read
            # these env vars when starting (ManagedJobEvent.start()) and write
            # it to disk.
            resources = launched_resources.assert_launchable()
            vcpus, mem = resources.cloud.get_vcpus_mem_from_instance_type(
                resources.instance_type)
            if vcpus is not None:
                env_vars['SKYPILOT_POD_CPU_CORE_LIMIT'] = str(vcpus)
            if mem is not None:
                env_vars['SKYPILOT_POD_MEMORY_GB_LIMIT'] = str(mem)

        # Cluster placement, accelerator, and provenance context for
        # skylet's heartbeat event (sky/skylet/events.py:
        # UsageHeartbeatReportEvent). cloud / region / zone / instance_type
        # / use_spot come from launched_resources directly — they reflect
        # the actual placement and aren't on cluster_info. The user hash
        # comes from the orchestrator process so the heartbeat carries
        # the launching user, not whatever identity the head node would
        # generate on its own.
        env_vars['SKYPILOT_HEARTBEAT_NUM_NODES'] = str(
            cluster_info.num_instances)
        env_vars['SKYPILOT_HEARTBEAT_USER'] = common_utils.get_user_hash()
        env_vars['SKYPILOT_HEARTBEAT_USE_SPOT'] = (
            '1' if launched_resources.use_spot else '0')
        if launched_resources.cloud is not None:
            env_vars['SKYPILOT_HEARTBEAT_CLOUD'] = str(launched_resources.cloud)
        if launched_resources.region is not None:
            env_vars['SKYPILOT_HEARTBEAT_REGION'] = launched_resources.region
        if launched_resources.zone is not None:
            env_vars['SKYPILOT_HEARTBEAT_ZONE'] = launched_resources.zone
        if launched_resources.instance_type is not None:
            env_vars['SKYPILOT_HEARTBEAT_INSTANCE_TYPE'] = (
                launched_resources.instance_type)
        accelerators = launched_resources.accelerators or {}
        if accelerators:
            gpu_type, count = next(iter(accelerators.items()))
            env_vars['SKYPILOT_HEARTBEAT_GPU_TYPE'] = str(gpu_type)
            env_vars['SKYPILOT_HEARTBEAT_GPUS_PER_NODE'] = str(int(count))

        # Quote values so shell metacharacters in any field (e.g. an
        # exotic K8s context derived region/zone, a hyphenated GPU type,
        # or a future env var that carries a path or sentence) cannot
        # break the export. shlex.quote is a no-op on plain alphanumerics.
        return '; '.join(
            f'export {k}={shlex.quote(v)}' for k, v in env_vars.items())

    runners = provision.get_command_runners(cluster_info.provider_name,
                                            cluster_info, **ssh_credentials)
    head_runner = runners[0]
    assert cluster_info.head_instance_id is not None, cluster_info
    log_path_abs = str(provision_logging.get_log_path())
    logger.info(f'Running command on head node: {MAYBE_SKYLET_RESTART_CMD}')
    # We need to source bashrc for skylet to make sure the idle-timer
    # (stop/down) event handler can access the path to the cloud CLIs.
    set_usage_run_id_cmd = _set_usage_run_id_cmd()
    # Set the skypilot environment variables, including the usage type, debug
    # info, other options, and cluster identity / accelerator context for
    # skylet's heartbeat event.
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
    if not file_mounts:
        return

    for dst, src in file_mounts.items():
        # TODO: We should use this trick to speed up file mounting:
        # https://stackoverflow.com/questions/1636889/how-can-i-configure-rsync-to-create-target-directory-on-remote-server
        full_src = os.path.abspath(os.path.expanduser(src))

        if os.path.isfile(full_src):
            mkdir_command = f'mkdir -p {os.path.dirname(dst)}'
        else:
            mkdir_command = f'mkdir -p {dst}'
        rc, stdout, stderr = runner.run_setup(mkdir_command,
                                              log_path=log_path,
                                              stream_logs=False,
                                              require_outputs=True)
        subprocess_utils.handle_returncode(
            rc,
            mkdir_command, ('Failed to run command before rsync '
                            f'{src} -> {dst}.'),
            stderr=stdout + stderr)

        runner.rsync_setup(
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


@common.log_function_start_end
@timeline.event
def setup_logging_on_cluster(logging_agent: logs.LoggingAgent,
                             cluster_name: resources_utils.ClusterName,
                             cluster_info: common.ClusterInfo,
                             ssh_credentials: Dict[str, Any]) -> None:
    """Setup logging agent (fluentbit) on all nodes after provisioning."""
    _hint_worker_log_path(cluster_name.name_on_cloud, cluster_info,
                          'logging_setup')

    @_auto_retry()
    def _setup_node(runner: command_runner.CommandRunner, log_path: str):
        cmd = logging_agent.get_setup_command(cluster_name)
        logger.info(f'Running command on node: {cmd}')
        returncode, stdout, stderr = runner.run(cmd,
                                                stream_logs=False,
                                                require_outputs=True,
                                                log_path=log_path,
                                                source_bashrc=True)
        if returncode:
            raise RuntimeError(f'Failed to setup logging agent\n{cmd}\n'
                               f'(exit code {returncode}). Error: '
                               f'===== stdout ===== \n{stdout}\n'
                               f'===== stderr ====={stderr}')

    _parallel_ssh_with_cache(_setup_node,
                             cluster_name.name_on_cloud,
                             stage_name='logging_setup',
                             digest=None,
                             cluster_info=cluster_info,
                             ssh_credentials=ssh_credentials)
