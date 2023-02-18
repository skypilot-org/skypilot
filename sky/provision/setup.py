"""Setup dependencies & services during provisioning."""
import os
from typing import List, Dict, Tuple
import hashlib
from concurrent import futures

from sky.utils import command_runner, subprocess_utils
from sky.provision import utils as provision_utils


def _parallel_ssh_with_cache(func, cluster_name: str, stage_name: str,
                             digest: str, ip_dict: Dict,
                             ssh_credentials: Dict[str, str]):
    pool = futures.ThreadPoolExecutor(max_workers=32)

    results = []
    for instance_id, (private_ip, public_ip) in ip_dict.items():
        runner = command_runner.SSHCommandRunner(public_ip, **ssh_credentials)
        wrapper = provision_utils.cache_func(cluster_name, instance_id,
                                             stage_name, digest)
        log_dir_abs = provision_utils.get_log_dir(cluster_name, instance_id)
        log_path_abs = str(log_dir_abs / (stage_name + '.log'))
        results.append(pool.submit(wrapper(func), runner, log_path_abs))

    for future in results:
        future.result()


def internal_dependencies_setup(cluster_name: str, setup_commands: List[str],
                                ip_dict: Dict[str, Tuple[str, str]],
                                ssh_credentials: Dict[str, str]):
    # compute the digest
    digests = []
    for cmd in setup_commands:
        digests.append(hashlib.sha256(cmd.encode()).digest())
    hasher = hashlib.sha256()
    for d in digests:
        hasher.update(d)
    digest = hasher.hexdigest()

    def _setup_node(runner: command_runner.SSHCommandRunner, log_path: str):
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
                             ip_dict=ip_dict,
                             ssh_credentials=ssh_credentials)


def start_ray(ssh_runners: List[command_runner.SSHCommandRunner],
              head_private_ip: str,
              check_ray_started: bool = False):
    if check_ray_started:
        returncode = ssh_runners[0].run('ray status', stream_logs=False)
        if returncode == 0:
            return

    ray_prlimit = (
        'which prlimit && for id in $(pgrep -f raylet/raylet); '
        'do sudo prlimit --nofile=1048576:1048576 --pid=$id || true; done;')

    returncode, stdout, stderr = ssh_runners[0].run(
        'ray stop; ray start --disable-usage-stats --head '
        '--port=6379 --object-manager-port=8076;' + ray_prlimit,
        stream_logs=False,
        require_outputs=True)
    if returncode:
        raise RuntimeError('Failed to start ray on the head node '
                           f'(exit code {returncode}). Error: '
                           f'===== stdout ===== \n{stdout}\n'
                           f'===== stderr ====={stderr}')

    def _setup_ray_worker(runner: command_runner.SSHCommandRunner):
        # for cmd in config_from_yaml['worker_start_ray_commands']:
        #     cmd = cmd.replace('$RAY_HEAD_IP', ip_list[0][0])
        #     runner.run(cmd)
        return runner.run(f'ray stop; ray start --disable-usage-stats '
                          f'--address={head_private_ip}:6379;' + ray_prlimit,
                          stream_logs=False,
                          require_outputs=True)

    results = subprocess_utils.run_in_parallel(_setup_ray_worker,
                                               ssh_runners[1:])
    for returncode, stdout, stderr in results:
        if returncode:
            raise RuntimeError('Failed to start ray on the worker node '
                               f'(exit code {returncode}). Error: '
                               f'===== stdout ===== \n{stdout}\n'
                               f'===== stderr ====={stderr}')


def start_skylet(ssh_runner: command_runner.SSHCommandRunner):
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


def _internal_file_mounts(file_mounts: Dict,
                          runner: command_runner.SSHCommandRunner,
                          log_path: str):
    if file_mounts is None or not file_mounts:
        return

    for dst, src in file_mounts.items():
        # We should use this trick to speed up file mounting:
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


def internal_file_mounts(cluster_name: str, file_mounts: Dict,
                         ip_dict: Dict[str, Tuple[str, str]],
                         ssh_credentials: Dict[str, str], wheel_hash: str):
    """Executes file mounts - rsyncing local files and
    copying from remote stores."""

    def _setup_node(runner: command_runner.SSHCommandRunner, log_path: str):
        _internal_file_mounts(file_mounts, runner, log_path)

    _parallel_ssh_with_cache(_setup_node,
                             cluster_name,
                             stage_name='internal_file_mounts',
                             digest=wheel_hash,
                             ip_dict=ip_dict,
                             ssh_credentials=ssh_credentials)
