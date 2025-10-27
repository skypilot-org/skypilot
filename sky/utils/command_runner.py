"""Runner for commands to be executed on the cluster."""
import enum
import hashlib
import os
import pathlib
import re
import shlex
import sys
import time
from typing import (Any, Callable, Dict, Iterable, List, Optional, Tuple, Type,
                    Union)

from sky import exceptions
from sky import sky_logging
from sky.skylet import constants
from sky.skylet import log_lib
from sky.utils import auth_utils
from sky.utils import common_utils
from sky.utils import context_utils
from sky.utils import control_master_utils
from sky.utils import git as git_utils
from sky.utils import subprocess_utils
from sky.utils import timeline

logger = sky_logging.init_logger(__name__)

# Pattern to extract home directory from command output
_HOME_DIR_PATTERN = re.compile(r'SKYPILOT_HOME_DIR: ([^\s\n]+)')

# Rsync options
# TODO(zhwu): This will print a per-file progress bar (with -P),
# shooting a lot of messages to the output. --info=progress2 is used
# to get a total progress bar, but it requires rsync>=3.1.0 and Mac
# OS has a default rsync==2.6.9 (16 years old).
RSYNC_DISPLAY_OPTION = '-Pavz'
# Legend
#   dir-merge: ignore file can appear in any subdir, applies to that
#     subdir downwards
# Note that "-" is mandatory for rsync and means all patterns in the ignore
# files are treated as *exclude* patterns.  Non-exclude patterns, e.g., "!
# do_not_exclude" doesn't work, even though git allows it.
# TODO(cooperc): Avoid using this, and prefer utils in storage_utils instead for
# consistency between bucket upload and rsync.
RSYNC_FILTER_SKYIGNORE = f'--filter=\'dir-merge,- {constants.SKY_IGNORE_FILE}\''
RSYNC_FILTER_GITIGNORE = f'--filter=\'dir-merge,- {constants.GIT_IGNORE_FILE}\''
# The git exclude file to support.
GIT_EXCLUDE = '.git/info/exclude'
RSYNC_EXCLUDE_OPTION = '--exclude-from={}'
# Owner and group metadata is not needed for downloads.
RSYNC_NO_OWNER_NO_GROUP_OPTION = '--no-owner --no-group'

_HASH_MAX_LENGTH = 10
_DEFAULT_CONNECT_TIMEOUT = 30


def _ssh_control_path(ssh_control_filename: Optional[str]) -> Optional[str]:
    """Returns a temporary path to be used as the ssh control path."""
    if ssh_control_filename is None:
        return None
    user_hash = common_utils.get_user_hash()
    path = f'/tmp/skypilot_ssh_{user_hash}/{ssh_control_filename}'
    os.makedirs(path, exist_ok=True)
    return path


# Disable sudo for root user. This is useful when the command is running in a
# docker container, i.e. image_id is a docker image.
ALIAS_SUDO_TO_EMPTY_FOR_ROOT_CMD = (
    '{ [ "$(whoami)" == "root" ] && function sudo() { "$@"; } || true; }')


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
        connect_timeout = _DEFAULT_CONNECT_TIMEOUT
    # Forked from Ray SSHOptions:
    # https://github.com/ray-project/ray/blob/master/python/ray/autoscaler/_private/command_runner.py
    # Do not allow agent forwarding because SkyPilot API server has access to
    # all user cluster private keys, which should not be all forwarded to
    # individual user clusters.
    arg_dict = {
        # SSH port
        'Port': port,
        # Suppresses initial fingerprint verification.
        'StrictHostKeyChecking': 'no',
        # SSH IP and fingerprint pairs no longer added to known_hosts.
        # This is to remove a 'REMOTE HOST IDENTIFICATION HAS CHANGED'
        # warning if a new node has the same IP as a previously
        # deleted node, because the fingerprints will not match in
        # that case.
        'UserKnownHostsFile': os.devnull,
        # Suppresses the warning messages, such as:
        #   Warning: Permanently added 'xx.xx.xx.xx' (EDxxx) to the list of
        #   known hosts.
        'LogLevel': 'ERROR',
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
    }
    # SSH Control will have a severe delay when using docker_ssh_proxy_command.
    # TODO(tian): Investigate why.
    #
    # We disable ControlMaster when ssh_proxy_command is used, because the
    # master connection will be idle although the connection might be shared
    # by other ssh commands that is not idle. In that case, user's custom proxy
    # command may drop the connection due to idle timeout, since it will only
    # see the idle master connection. It is an issue even with the
    # ServerAliveInterval set, since the keepalive message may not be recognized
    # by the custom proxy command, such as AWS SSM Session Manager.
    #
    # We also do not use ControlMaster when we use `kubectl port-forward`
    # to access Kubernetes pods over SSH+Proxycommand. This is because the
    # process running ProxyCommand is kept running as long as the ssh session
    # is running and the ControlMaster keeps the session, which results in
    # 'ControlPersist' number of seconds delay per ssh commands ran.
    if (ssh_control_name is not None and docker_ssh_proxy_command is None and
            ssh_proxy_command is None and not disable_control_master):
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


class CommandRunner:
    """Runner for commands to be executed on the cluster."""

    def __init__(self, node: Tuple[Any, Any], **kwargs):
        del kwargs  # Unused.
        self.node = node

    @property
    def node_id(self) -> str:
        return '-'.join(str(x) for x in self.node)

    def _get_remote_home_dir(self) -> str:
        # Use pattern matching to extract home directory.
        # Some container images print MOTD when login shells start, which can
        # contaminate command output. We use a unique pattern to extract the
        # actual home directory reliably.
        rc, output, stderr = self.run('echo "SKYPILOT_HOME_DIR: $(echo ~)"',
                                      require_outputs=True,
                                      separate_stderr=True,
                                      stream_logs=False)
        if rc != 0:
            raise ValueError('Failed to get remote home directory: '
                             f'{output + stderr}')

        # Extract home directory using pattern matching
        home_dir_match = _HOME_DIR_PATTERN.search(output)
        if home_dir_match:
            remote_home_dir = home_dir_match.group(1)
        else:
            raise ValueError('Failed to find remote home directory identifier: '
                             f'{output + stderr}')
        return remote_home_dir

    def _get_command_to_run(
        self,
        cmd: Union[str, List[str]],
        process_stream: bool,
        separate_stderr: bool,
        skip_num_lines: int,
        source_bashrc: bool = False,
        use_login: bool = True,
    ) -> str:
        """Returns the command to run."""
        if isinstance(cmd, list):
            cmd = ' '.join(cmd)

        # We need this to correctly run the cmd, and get the output.
        command = [
            '/bin/bash',
            '--login',
            '-c',
        ] if use_login else ['/bin/bash', '-c']
        if source_bashrc:
            command += [
                # Need this `-i` option to make sure `source ~/.bashrc` work.
                # Sourcing bashrc may take a few seconds causing overheads.
                '-i',
                shlex.quote(
                    f'true && source ~/.bashrc && export OMP_NUM_THREADS=1 '
                    f'PYTHONWARNINGS=ignore && ({cmd})'),
            ]
        else:
            # Optimization: this reduces the time for connecting to the remote
            # cluster by 1 second.
            # sourcing ~/.bashrc is not required for internal executions
            command += [
                shlex.quote('true && export OMP_NUM_THREADS=1 '
                            f'PYTHONWARNINGS=ignore && ({cmd})')
            ]
        if not separate_stderr:
            command.append('2>&1')
        if not process_stream and skip_num_lines:
            command += [
                # A hack to remove the following bash warnings (twice):
                #  bash: cannot set terminal process group
                #  bash: no job control in this shell
                f'| stdbuf -o0 tail -n +{skip_num_lines}',
                # This is required to make sure the executor of command can get
                # correct returncode, since linux pipe is used.
                '; exit ${PIPESTATUS[0]}'
            ]

        command_str = ' '.join(command)
        return command_str

    def _get_remote_home_dir_with_retry(
        self,
        max_retry: int,
        get_remote_home_dir: Callable[[], str],
    ) -> str:
        """Returns the remote home directory with retry."""
        backoff = common_utils.Backoff(initial_backoff=1, max_backoff_factor=5)
        retries_left = max_retry
        assert retries_left > 0, f'max_retry {max_retry} must be positive.'
        while retries_left >= 0:
            try:
                return get_remote_home_dir()
            except Exception:  # pylint: disable=broad-except
                if retries_left == 0:
                    raise
                sleep_time = backoff.current_backoff()
                logger.warning(f'Failed to get remote home dir '
                               f'- retrying in {sleep_time} seconds.')
                retries_left -= 1
                time.sleep(sleep_time)

    def _rsync(
            self,
            source: str,
            target: str,
            node_destination: Optional[str],
            up: bool,
            rsh_option: Optional[str],
            # Advanced options.
            log_path: str = os.devnull,
            stream_logs: bool = True,
            max_retry: int = 1,
            prefix_command: Optional[str] = None,
            get_remote_home_dir: Callable[[], str] = lambda: '~') -> None:
        """Builds the rsync command."""
        # Build command.
        rsync_command = []
        if prefix_command is not None:
            rsync_command.append(prefix_command)
        rsync_command += ['rsync', RSYNC_DISPLAY_OPTION]
        if not up:
            rsync_command.append(RSYNC_NO_OWNER_NO_GROUP_OPTION)

        # --filter
        # The source is a local path, so we need to resolve it.
        resolved_source = pathlib.Path(source).expanduser().resolve()
        if (resolved_source / constants.SKY_IGNORE_FILE).exists():
            rsync_command.append(RSYNC_FILTER_SKYIGNORE)
        else:
            rsync_command.append(RSYNC_FILTER_GITIGNORE)
            if up:
                # Build --exclude-from argument.
                if (resolved_source / GIT_EXCLUDE).exists():
                    # Ensure file exists; otherwise, rsync will error out.
                    #
                    # We shlex.quote() because the path may contain spaces:
                    #   'my dir/.git/info/exclude'
                    # Without quoting rsync fails.
                    rsync_command.append(
                        RSYNC_EXCLUDE_OPTION.format(
                            shlex.quote(str(resolved_source / GIT_EXCLUDE))))

        if rsh_option is not None:
            rsync_command.append(f'-e {shlex.quote(rsh_option)}')
        maybe_dest_prefix = ('' if node_destination is None else
                             f'{node_destination}:')

        if up:
            resolved_target = target
            if node_destination is None:
                # Is a local rsync. Directly resolve the target.
                resolved_target = str(
                    pathlib.Path(target).expanduser().resolve())
            else:
                if target.startswith('~'):
                    remote_home_dir = self._get_remote_home_dir_with_retry(
                        max_retry=max_retry,
                        get_remote_home_dir=get_remote_home_dir)
                    resolved_target = target.replace('~', remote_home_dir)
            full_source_str = str(resolved_source)
            if resolved_source.is_dir():
                full_source_str = os.path.join(full_source_str, '')
            rsync_command.extend([
                f'{full_source_str!r}',
                f'{maybe_dest_prefix}{resolved_target!r}',
            ])
        else:
            resolved_source = source
            if node_destination is None:
                resolved_target = str(
                    pathlib.Path(target).expanduser().resolve())
                resolved_source = str(
                    pathlib.Path(source).expanduser().resolve())
            else:
                resolved_target = os.path.expanduser(target)
                if source.startswith('~'):
                    remote_home_dir = self._get_remote_home_dir_with_retry(
                        max_retry=max_retry,
                        get_remote_home_dir=get_remote_home_dir)
                    resolved_source = source.replace('~', remote_home_dir)
            rsync_command.extend([
                f'{maybe_dest_prefix}{resolved_source!r}',
                f'{resolved_target!r}',
            ])
        command = ' '.join(rsync_command)
        logger.debug(f'Running rsync command: {command}')

        backoff = common_utils.Backoff(initial_backoff=5, max_backoff_factor=5)
        assert max_retry > 0, f'max_retry {max_retry} must be positive.'
        while max_retry >= 0:
            returncode, stdout, stderr = log_lib.run_with_log(
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
                                           stderr=stdout + stderr,
                                           stream_logs=stream_logs)

    @timeline.event
    def run(
            self,
            cmd: Union[str, List[str]],
            *,
            require_outputs: bool = False,
            # Advanced options.
            log_path: str = os.devnull,
            # If False, do not redirect stdout/stderr to optimize performance.
            process_stream: bool = True,
            stream_logs: bool = True,
            ssh_mode: SshMode = SshMode.NON_INTERACTIVE,
            separate_stderr: bool = False,
            connect_timeout: Optional[int] = None,
            source_bashrc: bool = False,
            skip_num_lines: int = 0,
            **kwargs) -> Union[int, Tuple[int, str, str]]:
        """Runs the command on the cluster.

        Args:
            cmd: The command to run.
            require_outputs: Whether to return the stdout/stderr of the command.
            log_path: Redirect stdout/stderr to the log_path.
            stream_logs: Stream logs to the stdout/stderr.
            ssh_mode: The mode to use for ssh.
                See SSHMode for more details.
            separate_stderr: Whether to separate stderr from stdout.
            connect_timeout: timeout in seconds for the ssh connection.
            source_bashrc: Whether to source the ~/.bashrc before running the
                command.
            skip_num_lines: The number of lines to skip at the beginning of the
                output. This is used when the output is not processed by
                SkyPilot but we still want to get rid of some warning messages,
                such as SSH warnings.

        Returns:
            returncode
            or
            A tuple of (returncode, stdout, stderr).
        """
        raise NotImplementedError

    @timeline.event
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
        raise NotImplementedError

    @classmethod
    def make_runner_list(
        cls: Type['CommandRunner'],
        node_list: Iterable[Any],
        **kwargs,
    ) -> List['CommandRunner']:
        """Helper function for creating runners with the same credentials"""
        return [cls(node, **kwargs) for node in node_list]

    def check_connection(self) -> bool:
        """Check if the connection to the remote machine is successful."""
        returncode = self.run('true', connect_timeout=5, stream_logs=False)
        return returncode == 0

    def close_cached_connection(self) -> None:
        """Close the cached connection to the remote machine."""
        pass

    def port_forward_command(
            self,
            port_forward: List[Tuple[int, int]],
            connect_timeout: int = 1,
            ssh_mode: SshMode = SshMode.INTERACTIVE) -> List[str]:
        """Command for forwarding ports from localhost to the remote machine.

        Args:
            port_forward: A list of ports to forward from the localhost to the
                remote host.
            connect_timeout: The timeout for the connection.
            ssh_mode: The mode to use for ssh.
                See SSHMode for more details.
        """
        raise NotImplementedError

    @timeline.event
    def git_clone(
        self,
        target_dir: str,
        *,
        # Advanced options.
        log_path: str = os.devnull,
        stream_logs: bool = True,
        connect_timeout: Optional[int] = None,
        max_retry: int = 1,
        envs_and_secrets: Optional[Dict[str, str]] = None,
    ) -> None:
        """Clones a Git repository on the remote machine using git_clone.sh.

        Note: Git environment variables (GIT_URL, GIT_BRANCH, GIT_TOKEN, etc.)
        must be set before calling this function.

        Args:
            target_dir: Target directory where the repository will be cloned.
            log_path: Redirect stdout/stderr to the log_path.
            stream_logs: Stream logs to the stdout/stderr.
            connect_timeout: timeout in seconds for the connection.
            max_retry: The maximum number of retries for the rsync command.
              This value should be non-negative.
            envs_and_secrets: Environment variables and secrets to be set
              before running the script.
        Raises:
            exceptions.CommandError: git clone command failed.
        """
        # Find the git_clone.sh script path
        git_clone_script_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'git_clone.sh')

        if not os.path.exists(git_clone_script_path):
            error_msg = f'git_clone.sh {git_clone_script_path} not found'
            logger.error(error_msg)
            raise exceptions.CommandError(1, '', error_msg, None)

        # Remote script path (use a unique name to avoid conflicts)
        script_hash = hashlib.md5(
            f'{self.node_id}_{target_dir}'.encode()).hexdigest()[:8]
        remote_script_path = f'/tmp/sky_git_clone_{script_hash}.sh'

        # Step 1: Transfer the script to remote machine using rsync
        logger.debug(
            f'Transferring git_clone.sh to {self.node_id}:{remote_script_path}')
        self.rsync(
            source=git_clone_script_path,
            target=remote_script_path,
            up=True,
            log_path=log_path,
            stream_logs=False  # Don't spam logs for script transfer
        )

        # Step 2: Execute the script on remote machine
        if target_dir.startswith('~'):
            remote_home_dir = self._get_remote_home_dir_with_retry(
                max_retry=max_retry,
                get_remote_home_dir=self._get_remote_home_dir)
            target_dir = target_dir.replace('~', remote_home_dir)
        quoted_target_dir = shlex.quote(target_dir)
        quoted_script_path = shlex.quote(remote_script_path)
        cmd = ''
        log_cmd = ''
        if envs_and_secrets:
            for key, value in envs_and_secrets.items():
                value = shlex.quote(value)
                cmd += f'export {key}={value} && '
                if (key == git_utils.GIT_TOKEN_ENV_VAR or
                        key == git_utils.GIT_SSH_KEY_ENV_VAR):
                    log_cmd += f'export {key}=******** && '
                else:
                    log_cmd += f'export {key}={value} && '
        exec_cmd = (f'bash {quoted_script_path} {quoted_target_dir} '
                    f'&& rm -f {quoted_script_path}')
        cmd += exec_cmd
        log_cmd += exec_cmd

        logger.debug(f'Running git clone script on {self.node_id}: {log_cmd}')

        backoff = common_utils.Backoff(initial_backoff=5, max_backoff_factor=5)
        assert max_retry > 0, f'max_retry {max_retry} must be positive.'
        while max_retry >= 0:
            returncode = self.run(cmd,
                                  log_path=log_path,
                                  stream_logs=stream_logs,
                                  connect_timeout=connect_timeout,
                                  require_outputs=False)
            if returncode == 0:
                break
            max_retry -= 1
            time.sleep(backoff.current_backoff())

        if returncode != 0:
            error_msg = f'Git clone failed on {self.node_id}: {target_dir}'
            logger.error(error_msg)
            raise exceptions.CommandError(returncode, log_cmd, error_msg, None)


class SSHCommandRunner(CommandRunner):
    """Runner for SSH commands."""

    def __init__(
        self,
        node: Tuple[str, int],
        ssh_user: str,
        ssh_private_key: str,
        ssh_control_name: Optional[str] = '__default__',
        ssh_proxy_command: Optional[str] = None,
        docker_user: Optional[str] = None,
        disable_control_master: Optional[bool] = False,
        port_forward_execute_remote_command: Optional[bool] = False,
    ):
        """Initialize SSHCommandRunner.

        Example Usage:
            runner = SSHCommandRunner(ip, ssh_user, ssh_private_key)
            runner.run('ls -l', mode=SshMode.NON_INTERACTIVE)
            runner.rsync(source, target, up=True)

        Args:
            node: (ip, port) The IP address and port of the remote machine.
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
            port_forward_execute_remote_command: bool; specifies whether to
                add -N to the port forwarding command. This is useful if you
                want to run a command on the remote machine to make sure the
                SSH tunnel is established.
        """
        super().__init__(node)
        ip, port = node
        self.ssh_private_key = ssh_private_key
        self.ssh_control_name = (
            None if ssh_control_name is None else hashlib.md5(
                ssh_control_name.encode()).hexdigest()[:_HASH_MAX_LENGTH])
        self._ssh_proxy_command = ssh_proxy_command
        self.disable_control_master = (
            disable_control_master or
            control_master_utils.should_disable_control_master())
        # ensure the ssh key files are created from the database
        auth_utils.create_ssh_key_files_from_db(ssh_private_key)
        if docker_user is not None:
            assert port is None or port == 22, (
                f'port must be None or 22 for docker_user, got {port}.')
            # When connecting via docker, the outer SSH hop points to the
            # container's sshd (localhost). Preserve the user proxy for the
            # inner hop that reaches the host VM, and clear the outer proxy to
            # avoid forwarding localhost through the jump host.
            inner_proxy_command = ssh_proxy_command
            inner_proxy_port = port or 22
            self._ssh_proxy_command = None
            self.ip = 'localhost'
            self.ssh_user = docker_user
            self.port = constants.DEFAULT_DOCKER_PORT
            if inner_proxy_command is not None:
                # Replace %h/%p placeholders with actual host values, since the
                # final destination from the perspective of the user proxy is
                # the host VM (ip, inner_proxy_port).
                inner_proxy_command = inner_proxy_command.replace('%h', ip)
                inner_proxy_command = inner_proxy_command.replace(
                    '%p', str(inner_proxy_port))
            self._docker_ssh_proxy_command = lambda ssh: ' '.join(
                ssh + ssh_options_list(ssh_private_key,
                                       None,
                                       ssh_proxy_command=inner_proxy_command,
                                       port=inner_proxy_port,
                                       disable_control_master=self.
                                       disable_control_master) +
                ['-W', '%h:%p', f'{ssh_user}@{ip}'])
        else:
            self.ip = ip
            self.ssh_user = ssh_user
            self.port = port
            self._docker_ssh_proxy_command = None
        self.port_forward_execute_remote_command = (
            port_forward_execute_remote_command)

    def port_forward_command(
            self,
            port_forward: List[Tuple[int, int]],
            connect_timeout: int = 1,
            ssh_mode: SshMode = SshMode.INTERACTIVE) -> List[str]:
        """Command for forwarding ports from localhost to the remote machine.

        Args:
            port_forward: A list of ports to forward from the local port to the
                remote port.
            connect_timeout: The timeout for the ssh connection.
            ssh_mode: The mode to use for ssh.
                See SSHMode for more details.

        Returns:
            The command for forwarding ports from localhost to the remote
            machine.
        """
        return self.ssh_base_command(ssh_mode=ssh_mode,
                                     port_forward=port_forward,
                                     connect_timeout=connect_timeout)

    def ssh_base_command(self, *, ssh_mode: SshMode,
                         port_forward: Optional[List[Tuple[int, int]]],
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
            for local, remote in port_forward:
                logger.debug(
                    f'Forwarding local port {local} to remote port {remote}.')
                if self.port_forward_execute_remote_command:
                    ssh += ['-L']
                else:
                    ssh += ['-NL']
                ssh += [f'{local}:localhost:{remote}']
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

    def close_cached_connection(self) -> None:
        """Close the cached connection to the remote machine.

        This is useful when we need to make the permission update effective of a
        ssh user, e.g. usermod -aG docker $USER.
        """
        if self.ssh_control_name is not None:
            control_path = _ssh_control_path(self.ssh_control_name)
            if control_path is not None:
                # Suppress the `Exit request sent.` output for this command
                # which would interrupt the CLI spinner.
                cmd = (f'ssh -O exit -S {control_path}/%C '
                       f'{self.ssh_user}@{self.ip} > /dev/null 2>&1')
                logger.debug(f'Closing cached connection {control_path!r} with '
                             f'cmd: {cmd}')
                log_lib.run_with_log(cmd,
                                     log_path=os.devnull,
                                     require_outputs=False,
                                     stream_logs=False,
                                     process_stream=False,
                                     shell=True)

    @timeline.event
    @context_utils.cancellation_guard
    def run(
            self,
            cmd: Union[str, List[str]],
            *,
            require_outputs: bool = False,
            port_forward: Optional[List[Tuple[int, int]]] = None,
            # Advanced options.
            log_path: str = os.devnull,
            # If False, do not redirect stdout/stderr to optimize performance.
            process_stream: bool = True,
            stream_logs: bool = True,
            ssh_mode: SshMode = SshMode.NON_INTERACTIVE,
            separate_stderr: bool = False,
            connect_timeout: Optional[int] = None,
            source_bashrc: bool = False,
            skip_num_lines: int = 0,
            **kwargs) -> Union[int, Tuple[int, str, str]]:
        """Uses 'ssh' to run 'cmd' on a node with ip.

        Args:
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
            connect_timeout: timeout in seconds for the ssh connection.
            source_bashrc: Whether to source the bashrc before running the
                command.
            skip_num_lines: The number of lines to skip at the beginning of the
                output. This is used when the output is not processed by
                SkyPilot but we still want to get rid of some warning messages,
                such as SSH warnings.

        Returns:
            returncode
            or
            A tuple of (returncode, stdout, stderr).
        """
        base_ssh_command = self.ssh_base_command(
            ssh_mode=ssh_mode,
            port_forward=port_forward,
            connect_timeout=connect_timeout)
        if ssh_mode == SshMode.LOGIN:
            assert isinstance(cmd, list), 'cmd must be a list for login mode.'
            command = base_ssh_command + cmd
            proc = subprocess_utils.run(command, shell=False, check=False)
            return proc.returncode, '', ''

        command_str = self._get_command_to_run(cmd,
                                               process_stream,
                                               separate_stderr,
                                               skip_num_lines=skip_num_lines,
                                               source_bashrc=source_bashrc)
        command = base_ssh_command + [shlex.quote(command_str)]

        log_dir = os.path.expanduser(os.path.dirname(log_path))
        os.makedirs(log_dir, exist_ok=True)

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

    @timeline.event
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
        rsh_option = f'ssh {ssh_options}'
        self._rsync(source,
                    target,
                    node_destination=f'{self.ssh_user}@{self.ip}',
                    up=up,
                    rsh_option=rsh_option,
                    log_path=log_path,
                    stream_logs=stream_logs,
                    max_retry=max_retry)


class KubernetesCommandRunner(CommandRunner):
    """Runner for Kubernetes commands."""

    _MAX_RETRIES_FOR_RSYNC = 3

    def __init__(
        self,
        node: Tuple[Tuple[str, Optional[str]], str],
        deployment: Optional[str] = None,
        **kwargs,
    ):
        """Initialize KubernetesCommandRunner.

        Example Usage:
            runner = KubernetesCommandRunner((namespace, context), pod_name))
            runner.run('ls -l')
            runner.rsync(source, target, up=True)

        Args:
            node: The namespace and pod_name of the remote machine.
        """
        del kwargs
        super().__init__(node)
        (self.namespace, self.context), self.pod_name = node
        self.deployment = deployment

    @property
    def node_id(self) -> str:
        return f'{self.context}-{self.namespace}-{self.pod_name}'

    @property
    def kube_identifier(self) -> str:
        if self.deployment is not None:
            return f'deployment/{self.deployment}'
        else:
            return f'pod/{self.pod_name}'

    def port_forward_command(
            self,
            port_forward: List[Tuple[int, int]],
            connect_timeout: int = 1,
            ssh_mode: SshMode = SshMode.INTERACTIVE) -> List[str]:
        """Command for forwarding ports from localhost to the remote machine.

        Args:
            port_forward: A list of ports to forward from the local port to the
                remote port. Currently, only one port is supported, i.e. the
                list should have only one element.
            connect_timeout: The timeout for the ssh connection.
            ssh_mode: The mode to use for ssh.
                See SSHMode for more details.
        """
        del ssh_mode  # unused
        assert port_forward and len(port_forward) == 1, (
            'Only one port is supported for Kubernetes port-forward.')
        kubectl_args = [
            '--pod-running-timeout', f'{connect_timeout}s', '-n', self.namespace
        ]
        # The same logic to either set `--context` to the k8s context where
        # the sky cluster is hosted, or `--kubeconfig` to /dev/null for
        # in-cluster k8s is used below in the `run()` method.
        if self.context:
            kubectl_args += ['--context', self.context]
        # If context is none, it means the cluster is hosted on in-cluster k8s.
        # In this case, we need to set KUBECONFIG to /dev/null to avoid looking
        # for the cluster in whatever active context is set in the kubeconfig.
        else:
            kubectl_args += ['--kubeconfig', '/dev/null']
        local_port, remote_port = port_forward[0]
        local_port_str = f'{local_port}' if local_port is not None else ''

        kubectl_cmd = [
            'kubectl',
            *kubectl_args,
            'port-forward',
            self.kube_identifier,
            f'{local_port_str}:{remote_port}',
        ]
        return kubectl_cmd

    @timeline.event
    @context_utils.cancellation_guard
    def run(
            self,
            cmd: Union[str, List[str]],
            *,
            port_forward: Optional[List[int]] = None,
            require_outputs: bool = False,
            # Advanced options.
            log_path: str = os.devnull,
            # If False, do not redirect stdout/stderr to optimize performance.
            process_stream: bool = True,
            stream_logs: bool = True,
            ssh_mode: SshMode = SshMode.NON_INTERACTIVE,
            separate_stderr: bool = False,
            connect_timeout: Optional[int] = None,
            source_bashrc: bool = False,
            skip_num_lines: int = 0,
            **kwargs) -> Union[int, Tuple[int, str, str]]:
        """Uses 'kubectl exec' to run 'cmd' on a pod or deployment by its
        name and namespace.

        Args:
            cmd: The command to run.
            port_forward: This should be None for k8s.

            Advanced options:

            require_outputs: Whether to return the stdout/stderr of the command.
            log_path: Redirect stdout/stderr to the log_path.
            stream_logs: Stream logs to the stdout/stderr.
            check: Check the success of the command.
            ssh_mode: The mode to use for ssh.
                See SSHMode for more details.
            separate_stderr: Whether to separate stderr from stdout.
            connect_timeout: timeout in seconds for the pod connection.
            source_bashrc: Whether to source the bashrc before running the
                command.
            skip_num_lines: The number of lines to skip at the beginning of the
                output. This is used when the output is not processed by
                SkyPilot but we still want to get rid of some warning messages,
                such as SSH warnings.

        Returns:
            returncode
            or
            A tuple of (returncode, stdout, stderr).
        """
        # TODO(zhwu): implement port_forward for k8s.
        assert port_forward is None, ('port_forward is not supported for k8s '
                                      f'for now, but got: {port_forward}')
        if connect_timeout is None:
            connect_timeout = _DEFAULT_CONNECT_TIMEOUT
        kubectl_args = [
            '--pod-running-timeout', f'{connect_timeout}s', '-n', self.namespace
        ]
        if self.context:
            kubectl_args += ['--context', self.context]
        # If context is none, it means we are using incluster auth. In this
        # case, need to set KUBECONFIG to /dev/null to avoid using kubeconfig.
        if self.context is None:
            kubectl_args += ['--kubeconfig', '/dev/null']

        kubectl_args += [self.kube_identifier]

        if ssh_mode == SshMode.LOGIN:
            assert isinstance(cmd, list), 'cmd must be a list for login mode.'
            base_cmd = ['kubectl', 'exec', '-it', *kubectl_args, '--']
            command = base_cmd + cmd
            proc = subprocess_utils.run(command, shell=False, check=False)
            return proc.returncode, '', ''

        kubectl_base_command = ['kubectl', 'exec']

        if ssh_mode == SshMode.INTERACTIVE:
            kubectl_base_command.append('-i')
        kubectl_base_command += [*kubectl_args, '--']

        command_str = self._get_command_to_run(cmd,
                                               process_stream,
                                               separate_stderr,
                                               skip_num_lines=skip_num_lines,
                                               source_bashrc=source_bashrc)
        command = kubectl_base_command + [
            # It is important to use /bin/bash -c here to make sure we quote the
            # command to be run properly. Otherwise, directly appending commands
            # after '--' will not work for some commands, such as '&&', '>' etc.
            '/bin/bash',
            '-c',
            shlex.quote(command_str)
        ]

        log_dir = os.path.expanduser(os.path.dirname(log_path))
        os.makedirs(log_dir, exist_ok=True)

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

    @timeline.event
    def rsync(
        self,
        source: str,
        target: str,
        *,
        up: bool,
        # Advanced options.
        log_path: str = os.devnull,
        stream_logs: bool = True,
        max_retry: int = _MAX_RETRIES_FOR_RSYNC,
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
        helper_path = shlex.quote(
            os.path.join(os.path.abspath(os.path.dirname(__file__)),
                         'kubernetes', 'rsync_helper.sh'))
        namespace_context = f'{self.namespace}+{self.context}'
        # Avoid rsync interpreting :, /, and + in namespace_context as the
        # default delimiter for options and arguments.
        # rsync_helper.sh will parse the namespace_context by reverting the
        # encoding and pass it to kubectl exec.
        encoded_namespace_context = (namespace_context.replace(
            '@', '%40').replace(':', '%3A').replace('/',
                                                    '%2F').replace('+', '%2B'))
        self._rsync(
            source,
            target,
            node_destination=f'{self.pod_name}@{encoded_namespace_context}',
            up=up,
            rsh_option=helper_path,
            log_path=log_path,
            stream_logs=stream_logs,
            max_retry=max_retry,
            prefix_command=f'chmod +x {helper_path} && ',
            # rsync with `kubectl` as the rsh command will cause ~/xx parsed as
            # /~/xx, so we need to replace ~ with the remote home directory. We
            # only need to do this when ~ is at the beginning of the path.
            get_remote_home_dir=self._get_remote_home_dir)


class LocalProcessCommandRunner(CommandRunner):
    """Runner for local process commands."""

    def __init__(self):
        super().__init__('local')

    @timeline.event
    @context_utils.cancellation_guard
    def run(
            self,
            cmd: Union[str, List[str]],
            *,
            require_outputs: bool = False,
            port_forward: Optional[List[Tuple[int, int]]] = None,
            # Advanced options.
            log_path: str = os.devnull,
            # If False, do not redirect stdout/stderr to optimize performance.
            process_stream: bool = True,
            stream_logs: bool = True,
            ssh_mode: SshMode = SshMode.NON_INTERACTIVE,
            separate_stderr: bool = False,
            connect_timeout: Optional[int] = None,
            source_bashrc: bool = False,
            skip_num_lines: int = 0,
            **kwargs) -> Union[int, Tuple[int, str, str]]:
        """Use subprocess to run the command."""
        del port_forward, ssh_mode, connect_timeout  # Unused.

        command_str = self._get_command_to_run(cmd,
                                               process_stream,
                                               separate_stderr,
                                               skip_num_lines=skip_num_lines,
                                               source_bashrc=source_bashrc,
                                               use_login=False)

        log_dir = os.path.expanduser(os.path.dirname(log_path))
        os.makedirs(log_dir, exist_ok=True)

        executable = None
        command = [command_str]
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
        command_str = ' '.join(command)
        # For local process, the API server might not have this python path
        # setup. But this command runner should only be triggered from the API
        # server (in controller consolidation mode), so we can safely replace
        # the python path with the executable of the API server.
        command_str = command_str.replace(constants.SKY_PYTHON_CMD,
                                          sys.executable)
        logger.debug(f'Running command locally: {command_str}')
        return log_lib.run_with_log(command_str,
                                    log_path,
                                    require_outputs=require_outputs,
                                    stream_logs=stream_logs,
                                    process_stream=process_stream,
                                    shell=True,
                                    executable=executable,
                                    **kwargs)

    @timeline.event
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
        """Use rsync to sync the source to the target."""
        self._rsync(source,
                    target,
                    node_destination=None,
                    up=up,
                    rsh_option=None,
                    log_path=log_path,
                    stream_logs=stream_logs,
                    max_retry=max_retry)
