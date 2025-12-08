"""Utilities for SSH Node Pools Deployment"""
import os
import random
import re
import subprocess
import sys
from typing import List, Optional, Set

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


def _get_used_localhost_ports() -> Set[int]:
    """Get SSH port forwardings already in use on localhost"""
    used_ports = set()

    # Get ports from netstat (works on macOS and Linux)
    try:
        if sys.platform == 'darwin':
            # macOS
            result = subprocess.run(['netstat', '-an', '-p', 'tcp'],
                                    capture_output=True,
                                    text=True,
                                    check=False)
        else:
            # Linux and other Unix-like systems
            result = subprocess.run(['netstat', '-tln'],
                                    capture_output=True,
                                    text=True,
                                    check=False)

        if result.returncode == 0:
            # Look for lines with 'localhost:<port>' or '127.0.0.1:<port>'
            for line in result.stdout.splitlines():
                if '127.0.0.1:' in line or 'localhost:' in line:
                    match = re.search(r':(64\d\d)\s', line)
                    if match:
                        port = int(match.group(1))
                        if 6400 <= port <= 6500:  # Only consider our range
                            used_ports.add(port)
    except (subprocess.SubprocessError, FileNotFoundError):
        # If netstat fails, try another approach
        pass

    # Also check ports from existing kubeconfig entries
    try:
        result = subprocess.run([
            'kubectl', 'config', 'view', '-o',
            'jsonpath=\'{.clusters[*].cluster.server}\''
        ],
                                capture_output=True,
                                text=True,
                                check=False)

        if result.returncode == 0:
            # Look for localhost URLs with ports
            for url in result.stdout.split():
                if 'localhost:' in url or '127.0.0.1:' in url:
                    match = re.search(r':(\d+)', url)
                    if match:
                        port = int(match.group(1))
                        if 6400 <= port <= 6500:  # Only consider our range
                            used_ports.add(port)
    except subprocess.SubprocessError:
        pass

    return used_ports


def get_available_port(start: int = 6443, end: int = 6499) -> int:
    """Get an available port in the given range that's not used by other tunnels"""
    used_ports = _get_used_localhost_ports()

    # Try to use port 6443 first if available for the first cluster
    if start == 6443 and start not in used_ports:
        return start

    # Otherwise find any available port in the range
    available_ports = list(set(range(start, end + 1)) - used_ports)

    if not available_ports:
        # If all ports are used, pick a random one from our range
        # (we'll terminate any existing connection in the setup)
        return random.randint(start, end)

    # Sort to get deterministic allocation
    available_ports.sort()
    return available_ports[0]
