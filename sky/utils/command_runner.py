"""Runner for commands to be executed on the cluster."""
import enum
import hashlib
import os
import pathlib
import shlex
import time
from typing import List, Optional, Tuple, Union

from sky import sky_logging
from sky.skylet import constants
from sky.skylet import log_lib
from sky.utils import common_utils
from sky.utils import subprocess_utils

logger = sky_logging.init_logger(__name__)

# The git exclude file to support.
GIT_EXCLUDE = '.git/info/exclude'
# Rsync options
RSYNC_DISPLAY_OPTION = '-Pavz'
# Legend
#   dir-merge: ignore file can appear in any subdir, applies to that
#     subdir downwards
# Note that "-" is mandatory for rsync and means all patterns in the ignore
# files are treated as *exclude* patterns.  Non-exclude patterns, e.g., "!
# do_not_exclude" doesn't work, even though git allows it.
RSYNC_FILTER_OPTION = '--filter=\'dir-merge,- .gitignore\''
RSYNC_EXCLUDE_OPTION = '--exclude-from={}'

_HASH_MAX_LENGTH = 10


def _ssh_control_path(ssh_control_filename: Optional[str]) -> Optional[str]:
    """Returns a temporary path to be used as the ssh control path."""
    if ssh_control_filename is None:
        return None
    user_hash = common_utils.get_user_hash()
    path = f'/tmp/skypilot_ssh_{user_hash}/{ssh_control_filename}'
    os.makedirs(path, exist_ok=True)
    return path


def ssh_options_list(
    ssh_private_key: Optional[str],
    ssh_control_name: Optional[str],
    *,
    ssh_proxy_command: Optional[str] = None,
    docker_ssh_proxy_command: Optional[str] = None,
    connect_timeout: Optional[int] = None,
    port: int = 22,
    disable_control_master: Optional[bool] = False,
) -> List[str]:
    """Returns a list of sane options for 'ssh'."""
    if connect_timeout is None:
        connect_timeout = 30
    # Forked from Ray SSHOptions:
    # https://github.com/ray-project/ray/blob/master/python/ray/autoscaler/_private/command_runner.py
    arg_dict = {
        # SSH port
        'Port': port,
        # Supresses initial fingerprint verification.
        'StrictHostKeyChecking': 'no',
        # SSH IP and fingerprint pairs no longer added to known_hosts.
        # This is to remove a 'REMOTE HOST IDENTIFICATION HAS CHANGED'
        # warning if a new node has the same IP as a previously
        # deleted node, because the fingerprints will not match in
        # that case.
        'UserKnownHostsFile': os.devnull,
        # Try fewer extraneous key pairs.
        'IdentitiesOnly': 'yes',
        # Abort if port forwarding fails (instead of just printing to
        # stderr).
        'ExitOnForwardFailure': 'yes',
        # Quickly kill the connection if network connection breaks (as
        # opposed to hanging/blocking).
        'ServerAliveInterval': 5,
        'ServerAliveCountMax': 3,
        # ConnectTimeout.
        'ConnectTimeout': f'{connect_timeout}s',
        # Agent forwarding for git.
        'ForwardAgent': 'yes',
    }
    # SSH Control will have a severe delay when using docker_ssh_proxy_command.
    # TODO(tian): Investigate why.
    # We also do not use ControlMaster when we use `kubectl port-forward`
    # to access Kubernetes pods over SSH+Proxycommand. This is because the
    # process running ProxyCommand is kept running as long as the ssh session
    # is running and the ControlMaster keeps the session, which results in
    # 'ControlPersist' number of seconds delay per ssh commands ran.
    if (ssh_control_name is not None and docker_ssh_proxy_command is None and
            not disable_control_master):
        arg_dict.update({
            # Control path: important optimization as we do multiple ssh in one
            # sky.launch().
            'ControlMaster': 'auto',
            'ControlPath': f'{_ssh_control_path(ssh_control_name)}/%C',
            'ControlPersist': '300s',
        })
    ssh_key_option = [
        '-i',
        ssh_private_key,
    ] if ssh_private_key is not None else []

    if docker_ssh_proxy_command is not None:
        logger.debug(f'--- Docker SSH Proxy: {docker_ssh_proxy_command} ---')
        arg_dict.update({
            'ProxyCommand': shlex.quote(docker_ssh_proxy_command),
        })

    if ssh_proxy_command is not None:
        logger.debug(f'--- Proxy: {ssh_proxy_command} ---')
        arg_dict.update({
            # Due to how log_lib.run_with_log() works (using shell=True) we
            # must quote this value.
            'ProxyCommand': shlex.quote(ssh_proxy_command),
        })

    return ssh_key_option + [
        x for y in (['-o', f'{k}={v}']
                    for k, v in arg_dict.items()
                    if v is not None) for x in y
    ]


class SshMode(enum.Enum):
    """Enum for SSH mode."""
    # Do not allocating pseudo-tty to avoid user input corrupting outputs.
    NON_INTERACTIVE = 0
    # Allocate a pseudo-tty, quit the ssh session after the cmd finishes.
    # Be careful of this mode, as ctrl-c will be passed to remote process.
    INTERACTIVE = 1
    # Allocate a pseudo-tty and log into the ssh session.
    LOGIN = 2


class SSHCommandRunner:
    """Runner for SSH commands."""

    def __init__(
        self,
        ip: str,
        ssh_user: str,
        ssh_private_key: str,
        ssh_control_name: Optional[str] = '__default__',
        ssh_proxy_command: Optional[str] = None,
        port: int = 22,
        docker_user: Optional[str] = None,
        disable_control_master: Optional[bool] = False,
    ):
        """Initialize SSHCommandRunner.

        Example Usage:
            runner = SSHCommandRunner(ip, ssh_user, ssh_private_key)
            runner.run('ls -l', mode=SshMode.NON_INTERACTIVE)
            runner.rsync(source, target, up=True)

        Args:
            ip: The IP address of the remote machine.
            ssh_private_key: The path to the private key to use for ssh.
            ssh_user: The user to use for ssh.
            ssh_control_name: The files name of the ssh_control to use. This is
                used to avoid confliction between clusters for creating ssh
                control files. It can simply be the cluster_name or any name
                that can distinguish between clusters.
            ssh_proxy_command: Optional, the value to pass to '-o
                ProxyCommand'. Useful for communicating with clusters without
                public IPs using a "jump server".
            port: The port to use for ssh.
            docker_user: The docker user to use for ssh. If specified, the
                command will be run inside a docker container which have a ssh
                server running at port sky.skylet.constants.DEFAULT_DOCKER_PORT
            disable_control_master: bool; specifies either or not the ssh
                command will utilize ControlMaster. We currently disable
                it for k8s instance.
        """
        self.ssh_private_key = ssh_private_key
        self.ssh_control_name = (
            None if ssh_control_name is None else hashlib.md5(
                ssh_control_name.encode()).hexdigest()[:_HASH_MAX_LENGTH])
        self._ssh_proxy_command = ssh_proxy_command
        self.disable_control_master = disable_control_master
        if docker_user is not None:
            assert port is None or port == 22, (
                f'port must be None or 22 for docker_user, got {port}.')
            # Already checked in resources
            assert ssh_proxy_command is None, (
                'ssh_proxy_command is not supported when using docker.')
            self.ip = 'localhost'
            self.ssh_user = docker_user
            self.port = constants.DEFAULT_DOCKER_PORT
            self._docker_ssh_proxy_command = lambda ssh: ' '.join(
                ssh + ssh_options_list(ssh_private_key, None
                                      ) + ['-W', '%h:%p', f'{ssh_user}@{ip}'])
        else:
            self.ip = ip
            self.ssh_user = ssh_user
            self.port = port
            self._docker_ssh_proxy_command = None

    @staticmethod
    def make_runner_list(
        ip_list: List[str],
        ssh_user: str,
        ssh_private_key: str,
        ssh_control_name: Optional[str] = None,
        ssh_proxy_command: Optional[str] = None,
        disable_control_master: Optional[bool] = False,
        port_list: Optional[List[int]] = None,
        docker_user: Optional[str] = None,
    ) -> List['SSHCommandRunner']:
        """Helper function for creating runners with the same ssh credentials"""
        if not port_list:
            port_list = [22] * len(ip_list)
        return [
            SSHCommandRunner(ip, ssh_user, ssh_private_key, ssh_control_name,
                             ssh_proxy_command, port, docker_user,
                             disable_control_master)
            for ip, port in zip(ip_list, port_list)
        ]

    def _ssh_base_command(self, *, ssh_mode: SshMode,
                          port_forward: Optional[List[int]],
                          connect_timeout: Optional[int]) -> List[str]:
        ssh = ['ssh']
        if ssh_mode == SshMode.NON_INTERACTIVE:
            # Disable pseudo-terminal allocation. Otherwise, the output of
            # ssh will be corrupted by the user's input.
            ssh += ['-T']
        else:
            # Force pseudo-terminal allocation for interactive/login mode.
            ssh += ['-tt']
        if port_forward is not None:
            for port in port_forward:
                local = remote = port
                logger.info(
                    f'Forwarding port {local} to port {remote} on localhost.')
                ssh += ['-L', f'{remote}:localhost:{local}']
        if self._docker_ssh_proxy_command is not None:
            docker_ssh_proxy_command = self._docker_ssh_proxy_command(ssh)
        else:
            docker_ssh_proxy_command = None
        return ssh + ssh_options_list(
            self.ssh_private_key,
            self.ssh_control_name,
            ssh_proxy_command=self._ssh_proxy_command,
            docker_ssh_proxy_command=docker_ssh_proxy_command,
            port=self.port,
            connect_timeout=connect_timeout,
            disable_control_master=self.disable_control_master) + [
                f'{self.ssh_user}@{self.ip}'
            ]

    def run(
            self,
            cmd: Union[str, List[str]],
            *,
            require_outputs: bool = False,
            port_forward: Optional[List[int]] = None,
            # Advanced options.
            log_path: str = os.devnull,
            # If False, do not redirect stdout/stderr to optimize performance.
            process_stream: bool = True,
            stream_logs: bool = True,
            ssh_mode: SshMode = SshMode.NON_INTERACTIVE,
            separate_stderr: bool = False,
            connect_timeout: Optional[int] = None,
            **kwargs) -> Union[int, Tuple[int, str, str]]:
        """Uses 'ssh' to run 'cmd' on a node with ip.

        Args:
            ip: The IP address of the node.
            cmd: The command to run.
            port_forward: A list of ports to forward from the localhost to the
            remote host.

            Advanced options:

            require_outputs: Whether to return the stdout/stderr of the command.
            log_path: Redirect stdout/stderr to the log_path.
            stream_logs: Stream logs to the stdout/stderr.
            check: Check the success of the command.
            ssh_mode: The mode to use for ssh.
                See SSHMode for more details.
            separate_stderr: Whether to separate stderr from stdout.


        Returns:
            returncode
            or
            A tuple of (returncode, stdout, stderr).
        """
        base_ssh_command = self._ssh_base_command(
            ssh_mode=ssh_mode,
            port_forward=port_forward,
            connect_timeout=connect_timeout)
        if ssh_mode == SshMode.LOGIN:
            assert isinstance(cmd, list), 'cmd must be a list for login mode.'
            command = base_ssh_command + cmd
            proc = subprocess_utils.run(command, shell=False, check=False)
            return proc.returncode, '', ''
        if isinstance(cmd, list):
            cmd = ' '.join(cmd)

        log_dir = os.path.expanduser(os.path.dirname(log_path))
        os.makedirs(log_dir, exist_ok=True)
        # We need this to correctly run the cmd, and get the output.
        command = [
            'bash',
            '--login',
            '-c',
            # Need this `-i` option to make sure `source ~/.bashrc` work.
            '-i',
        ]

        command += [
            shlex.quote(f'true && source ~/.bashrc && export OMP_NUM_THREADS=1 '
                        f'PYTHONWARNINGS=ignore && ({cmd})'),
        ]
        if not separate_stderr:
            command.append('2>&1')
        if not process_stream and ssh_mode == SshMode.NON_INTERACTIVE:
            command += [
                # A hack to remove the following bash warnings (twice):
                #  bash: cannot set terminal process group
                #  bash: no job control in this shell
                '| stdbuf -o0 tail -n +5',
                # This is required to make sure the executor of command can get
                # correct returncode, since linux pipe is used.
                '; exit ${PIPESTATUS[0]}'
            ]

        command_str = ' '.join(command)
        command = base_ssh_command + [shlex.quote(command_str)]

        executable = None
        if not process_stream:
            if stream_logs:
                command += [
                    f'| tee {log_path}',
                    # This also requires the executor to be '/bin/bash' instead
                    # of the default '/bin/sh'.
                    '; exit ${PIPESTATUS[0]}'
                ]
            else:
                command += [f'> {log_path}']
            executable = '/bin/bash'
        return log_lib.run_with_log(' '.join(command),
                                    log_path,
                                    require_outputs=require_outputs,
                                    stream_logs=stream_logs,
                                    process_stream=process_stream,
                                    shell=True,
                                    executable=executable,
                                    **kwargs)

    def rsync(
        self,
        source: str,
        target: str,
        *,
        up: bool,
        # Advanced options.
        log_path: str = os.devnull,
        stream_logs: bool = True,
        max_retry: int = 1,
    ) -> None:
        """Uses 'rsync' to sync 'source' to 'target'.

        Args:
            source: The source path.
            target: The target path.
            up: The direction of the sync, True for local to cluster, False
              for cluster to local.
            log_path: Redirect stdout/stderr to the log_path.
            stream_logs: Stream logs to the stdout/stderr.
            max_retry: The maximum number of retries for the rsync command.
              This value should be non-negative.

        Raises:
            exceptions.CommandError: rsync command failed.
        """
        # Build command.
        # TODO(zhwu): This will print a per-file progress bar (with -P),
        # shooting a lot of messages to the output. --info=progress2 is used
        # to get a total progress bar, but it requires rsync>=3.1.0 and Mac
        # OS has a default rsync==2.6.9 (16 years old).
        rsync_command = ['rsync', RSYNC_DISPLAY_OPTION]

        # --filter
        rsync_command.append(RSYNC_FILTER_OPTION)

        if up:
            # The source is a local path, so we need to resolve it.
            # --exclude-from
            resolved_source = pathlib.Path(source).expanduser().resolve()
            if (resolved_source / GIT_EXCLUDE).exists():
                # Ensure file exists; otherwise, rsync will error out.
                #
                # We shlex.quote() because the path may contain spaces:
                #   'my dir/.git/info/exclude'
                # Without quoting rsync fails.
                rsync_command.append(
                    RSYNC_EXCLUDE_OPTION.format(
                        shlex.quote(str(resolved_source / GIT_EXCLUDE))))

        if self._docker_ssh_proxy_command is not None:
            docker_ssh_proxy_command = self._docker_ssh_proxy_command(['ssh'])
        else:
            docker_ssh_proxy_command = None
        ssh_options = ' '.join(
            ssh_options_list(
                self.ssh_private_key,
                self.ssh_control_name,
                ssh_proxy_command=self._ssh_proxy_command,
                docker_ssh_proxy_command=docker_ssh_proxy_command,
                port=self.port,
                disable_control_master=self.disable_control_master))
        rsync_command.append(f'-e "ssh {ssh_options}"')
        # To support spaces in the path, we need to quote source and target.
        # rsync doesn't support '~' in a quoted local path, but it is ok to
        # have '~' in a quoted remote path.
        if up:
            full_source_str = str(resolved_source)
            if resolved_source.is_dir():
                full_source_str = os.path.join(full_source_str, '')
            rsync_command.extend([
                f'{full_source_str!r}',
                f'{self.ssh_user}@{self.ip}:{target!r}',
            ])
        else:
            rsync_command.extend([
                f'{self.ssh_user}@{self.ip}:{source!r}',
                f'{os.path.expanduser(target)!r}',
            ])
        command = ' '.join(rsync_command)

        backoff = common_utils.Backoff(initial_backoff=5, max_backoff_factor=5)
        while max_retry >= 0:
            returncode, _, stderr = log_lib.run_with_log(
                command,
                log_path=log_path,
                stream_logs=stream_logs,
                shell=True,
                require_outputs=True)
            if returncode == 0:
                break
            max_retry -= 1
            time.sleep(backoff.current_backoff())

        direction = 'up' if up else 'down'
        error_msg = (f'Failed to rsync {direction}: {source} -> {target}. '
                     'Ensure that the network is stable, then retry.')
        subprocess_utils.handle_returncode(returncode,
                                           command,
                                           error_msg,
                                           stderr=stderr,
                                           stream_logs=stream_logs)

    def check_connection(self) -> bool:
        """Check if the connection to the remote machine is successful."""
        returncode = self.run('true', connect_timeout=5, stream_logs=False)
        if returncode:
            return False
        return True
