"""Utilities for SSH Node Pools Deployment"""
import os
import subprocess
from typing import List, Optional

import colorama

from sky import sky_logging
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)


def check_ssh_cluster_dependencies(
        raise_error: bool = True) -> Optional[List[str]]:
    """Checks if the dependencies for ssh cluster are installed.

    Args:
        raise_error: set to true when the dependency needs to be present.
            set to false for `sky check`, where reason strings are compiled
            at the end.

    Returns: the reasons list if there are missing dependencies.
    """
    # error message
    jq_message = ('`jq` is required to setup ssh cluster.')

    # save
    reasons = []
    required_binaries = []

    # Ensure jq is installed
    try:
        subprocess.run(['jq', '--version'],
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        required_binaries.append('jq')
        reasons.append(jq_message)

    if required_binaries:
        reasons.extend([
            'On Debian/Ubuntu, install the missing dependenc(ies) with:',
            f'  $ sudo apt install {" ".join(required_binaries)}',
            'On MacOS, install with: ',
            f'  $ brew install {" ".join(required_binaries)}',
        ])
        if raise_error:
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('\n'.join(reasons))
        return reasons
    return None


def run_command(cmd, shell=False, silent=False):
    """Run a local command and return the output."""
    process = subprocess.run(cmd,
                             shell=shell,
                             capture_output=True,
                             text=True,
                             check=False)
    if process.returncode != 0:
        if not silent:
            logger.error(f'{colorama.Fore.RED}Error executing command: {cmd}\n'
                         f'{colorama.Style.RESET_ALL}STDOUT: {process.stdout}\n'
                         f'STDERR: {process.stderr}')
        return None
    return process.stdout.strip()


def get_effective_host_ip(hostname: str) -> str:
    """Get the effective IP for a hostname from SSH config."""
    try:
        result = subprocess.run(['ssh', '-G', hostname],
                                capture_output=True,
                                text=True,
                                check=False)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith('hostname '):
                    return line.split(' ', 1)[1].strip()
    except Exception:  # pylint: disable=broad-except
        pass
    return hostname  # Return the original hostname if lookup fails


def run_remote(node,
               cmd,
               user='',
               ssh_key='',
               connect_timeout=30,
               use_ssh_config=False,
               print_output=False,
               use_shell=False,
               silent=False):
    """Run a command on a remote machine via SSH."""
    ssh_cmd: List[str]
    if use_ssh_config:
        # Use SSH config for connection parameters
        ssh_cmd = ['ssh', node, cmd]
    else:
        # Use explicit parameters
        ssh_cmd = [
            'ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'IdentitiesOnly=yes',
            '-o', f'ConnectTimeout={connect_timeout}', '-o',
            'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3'
        ]

        if ssh_key:
            if not os.path.isfile(ssh_key):
                raise ValueError(f'SSH key not found: {ssh_key}')
            ssh_cmd.extend(['-i', ssh_key])

        ssh_cmd.append(f'{user}@{node}' if user else node)
        ssh_cmd.append(cmd)

    subprocess_cmd = ' '.join(ssh_cmd) if use_shell else ssh_cmd
    process = subprocess.run(subprocess_cmd,
                             capture_output=True,
                             text=True,
                             check=False,
                             shell=use_shell)
    if process.returncode != 0:
        if not silent:
            logger.error(f'{colorama.Fore.RED}Error executing command {cmd} on '
                         f'{node}:{colorama.Style.RESET_ALL} {process.stderr}')
        return None
    if print_output:
        logger.info(process.stdout)
    return process.stdout.strip()


def ensure_directory_exists(path):
    """Ensure the directory for the specified file path exists."""
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)


def check_gpu(node, user, ssh_key, use_ssh_config=False, is_head=False):
    """Check if a node has a GPU."""
    cmd = ('command -v nvidia-smi &> /dev/null && '
           'nvidia-smi --query-gpu=gpu_name --format=csv,noheader')
    result = run_remote(node,
                        cmd,
                        user,
                        ssh_key,
                        use_ssh_config=use_ssh_config,
                        silent=True)
    if result is not None:
        # Check that all GPUs have the same type.
        # Currently, SkyPilot does not support heterogeneous GPU node
        # (i.e. more than one GPU type on the same node).
        gpu_names = {
            line.strip() for line in result.splitlines() if line.strip()
        }
        if not gpu_names:
            # This can happen if nvidia-smi returns only whitespace.
            # Set result to None to ensure this function returns False.
            result = None
        elif len(gpu_names) > 1:
            # Sort for a deterministic error message.
            sorted_gpu_names = sorted(list(gpu_names))
            raise RuntimeError(
                f'Node {node} has more than one GPU types '
                f'({", ".join(sorted_gpu_names)}). '
                'SkyPilot does not support a node with multiple GPU types.')
        else:
            logger.info(f'{colorama.Fore.YELLOW}➜ GPU {list(gpu_names)[0]} '
                        f'detected on {"head" if is_head else "worker"} '
                        f'node ({node}).{colorama.Style.RESET_ALL}')
    return result is not None
