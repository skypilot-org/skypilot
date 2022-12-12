"""Runner for commands to be executed on the cluster."""
import getpass
import enum
import hashlib
import os
import pathlib
import shlex
from typing import List, Optional, Tuple, Union

from sky import sky_logging
from sky.utils import subprocess_utils
from sky.skylet import log_lib

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
    username = getpass.getuser()
    path = (f'/tmp/skypilot_ssh_{username}/{ssh_control_filename}')
    os.makedirs(path, exist_ok=True)
    return path


def ssh_options_list(ssh_private_key: Optional[str],
                     ssh_control_name: Optional[str],
                     *,
                     timeout=30) -> List[str]:
    """Returns a list of sane options for 'ssh'."""
    # Forked from Ray SSHOptions:
    # https://github.com/ray-project/ray/blob/master/python/ray/autoscaler/_private/command_runner.py
    arg_dict = {
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
        'ConnectTimeout': f'{timeout}s',
        # Agent forwarding for git.
        'ForwardAgent': 'yes',
    }
    if ssh_control_name is not None:
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
        """
        self.ip = ip
        self.ssh_user = ssh_user
        self.ssh_private_key = ssh_private_key
        self.ssh_control_name = (
            None if ssh_control_name is None else hashlib.md5(
                ssh_control_name.encode()).hexdigest()[:_HASH_MAX_LENGTH])

    @staticmethod
    def make_runner_list(
            ip_list: List[str],
            ssh_user: str,
            ssh_private_key: str,
            ssh_control_name: Optional[str] = None) -> List['SSHCommandRunner']:
        """Helper function for creating runners with the same ssh credentials"""
        return [
            SSHCommandRunner(ip, ssh_user, ssh_private_key, ssh_control_name)
            for ip in ip_list
        ]

    def _ssh_base_command(self, *, ssh_mode: SshMode,
                          port_forward: Optional[List[int]]) -> List[str]:
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
        return ssh + ssh_options_list(
            self.ssh_private_key,
            self.ssh_control_name) + [f'{self.ssh_user}@{self.ip}']

    def run(
            self,
            cmd: Union[str, List[str]],
            *,
            port_forward: Optional[List[int]] = None,
            # Advanced options.
            require_outputs: bool = False,
            log_path: str = os.devnull,
            # If False, do not redirect stdout/stderr to optimize performance.
            process_stream: bool = True,
            stream_logs: bool = True,
            ssh_mode: SshMode = SshMode.NON_INTERACTIVE,
            separate_stderr: bool = False,
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
        base_ssh_command = self._ssh_base_command(ssh_mode=ssh_mode,
                                                  port_forward=port_forward)
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

        command = ' '.join(command)
        command = base_ssh_command + [shlex.quote(command)]

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
                                    stream_logs,
                                    process_stream=process_stream,
                                    require_outputs=require_outputs,
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
    ) -> None:
        """Uses 'rsync' to sync 'source' to 'target'.

        Args:
            source: The source path.
            target: The target path.
            up: The direction of the sync, True for local to cluster, False
              for cluster to local.
            log_path: Redirect stdout/stderr to the log_path.
            stream_logs: Stream logs to the stdout/stderr.

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

        # --exclude-from
        resolved_source = pathlib.Path(source).expanduser().resolve()
        if (resolved_source / GIT_EXCLUDE).exists():
            # Ensure file exists; otherwise, rsync will error out.
            rsync_command.append(
                RSYNC_EXCLUDE_OPTION.format(str(resolved_source / GIT_EXCLUDE)))

        # rsync doesn't support '~' in a quoted target path. need to expand it.
        full_source_str = str(resolved_source)
        if resolved_source.is_dir():
            full_source_str = os.path.join(full_source_str, '')

        ssh_options = ' '.join(
            ssh_options_list(self.ssh_private_key, self.ssh_control_name))
        rsync_command.append(f'-e "ssh {ssh_options}"')
        # To support spaces in the path, we need to quote source and target.
        if up:
            rsync_command.extend([
                f'{full_source_str!r}',
                f'{self.ssh_user}@{self.ip}:{target!r}',
            ])
        else:
            rsync_command.extend([
                f'{self.ssh_user}@{self.ip}:{full_source_str!r}',
                f'{target!r}',
            ])
        command = ' '.join(rsync_command)

        returncode, _, stderr = log_lib.run_with_log(command,
                                                     log_path=log_path,
                                                     stream_logs=stream_logs,
                                                     shell=True,
                                                     require_outputs=True)

        direction = 'up' if up else 'down'
        subprocess_utils.handle_returncode(
            returncode,
            command,
            f'Failed to rsync {direction}: {source} -> {target}',
            stderr=stderr,
            stream_logs=stream_logs)
