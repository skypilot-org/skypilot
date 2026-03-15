"""Utilities to setup SSH Tunnel"""
import os
import random
import re
import subprocess
import sys
from typing import Set

import colorama

from sky import sky_logging
from sky.ssh_node_pools import constants
from sky.ssh_node_pools.deploy import utils as deploy_utils

logger = sky_logging.init_logger(__name__)

# Get the directory of this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


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
    """Get an available port in the given range not used by other tunnels"""
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


def setup_kubectl_ssh_tunnel(head_node,
                             ssh_user,
                             ssh_key,
                             context_name,
                             use_ssh_config=False):
    """Set up kubeconfig exec credential plugin for SSH tunnel"""
    logger.info(f'{colorama.Fore.YELLOW}➜ Setting up SSH tunnel for '
                f'Kubernetes API access...{colorama.Style.RESET_ALL}')

    # Get an available port for this cluster
    port = get_available_port()

    # Paths to scripts
    tunnel_script = os.path.join(SCRIPT_DIR, 'tunnel', 'ssh-tunnel.sh')

    # Make sure scripts are executable
    os.chmod(tunnel_script, 0o755)

    # Certificate files
    client_cert_file = os.path.join(constants.NODE_POOLS_INFO_DIR,
                                    f'{context_name}-cert.pem')
    client_key_file = os.path.join(constants.NODE_POOLS_INFO_DIR,
                                   f'{context_name}-key.pem')

    # Update kubeconfig to use localhost with the selected port
    deploy_utils.run_command([
        'kubectl', 'config', 'set-cluster', context_name,
        f'--server=https://127.0.0.1:{port}', '--insecure-skip-tls-verify=true'
    ])

    # Build the exec args list based on auth method
    exec_args = [
        '--exec-command', tunnel_script, '--exec-api-version',
        'client.authentication.k8s.io/v1beta1'
    ]

    # Set credential TTL to force frequent tunnel checks
    ttl_seconds = 30

    # Verify if we have extracted certificate data files
    has_cert_files = os.path.isfile(client_cert_file) and os.path.isfile(
        client_key_file)
    if has_cert_files:
        logger.info(f'{colorama.Fore.GREEN}Client certificate data extracted '
                    'and will be used for authentication'
                    f'{colorama.Style.RESET_ALL}')

    if use_ssh_config:
        deploy_utils.run_command(
            ['kubectl', 'config', 'set-credentials', context_name] + exec_args +
            [
                '--exec-arg=--context', f'--exec-arg={context_name}',
                '--exec-arg=--port', f'--exec-arg={port}', '--exec-arg=--ttl',
                f'--exec-arg={ttl_seconds}', '--exec-arg=--use-ssh-config',
                '--exec-arg=--host', f'--exec-arg={head_node}'
            ])
    else:
        deploy_utils.run_command(
            ['kubectl', 'config', 'set-credentials', context_name] + exec_args +
            [
                '--exec-arg=--context', f'--exec-arg={context_name}',
                '--exec-arg=--port', f'--exec-arg={port}', '--exec-arg=--ttl',
                f'--exec-arg={ttl_seconds}', '--exec-arg=--host',
                f'--exec-arg={head_node}', '--exec-arg=--user',
                f'--exec-arg={ssh_user}', '--exec-arg=--ssh-key',
                f'--exec-arg={ssh_key}'
            ])

    logger.info(f'{colorama.Fore.GREEN}✔ SSH tunnel configured through '
                'kubectl credential plugin on port '
                f'{port}{colorama.Style.RESET_ALL}')
    logger.info('Your kubectl connection is now tunneled through SSH '
                f'(port {port}).')
    logger.info('This tunnel will be automatically established when needed.')
    logger.info(f'Credential TTL set to {ttl_seconds}s to ensure tunnel '
                'health is checked frequently.')
    return port


def cleanup_kubectl_ssh_tunnel(cluster_name, context_name):
    """Clean up the SSH tunnel for a specific context"""
    logger.info(f'{colorama.Fore.YELLOW}➜ Cleaning up SSH tunnel for '
                f'`{cluster_name}`...{colorama.Style.RESET_ALL}')

    # Path to cleanup script
    cleanup_script = os.path.join(SCRIPT_DIR, 'tunnel', 'cleanup-tunnel.sh')

    # Make sure script is executable
    if os.path.exists(cleanup_script):
        os.chmod(cleanup_script, 0o755)

        # Run the cleanup script
        subprocess.run([cleanup_script, context_name],
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       check=False)
        logger.info(f'{colorama.Fore.GREEN}✔ SSH tunnel for `{cluster_name}` '
                    f'cleaned up.{colorama.Style.RESET_ALL}')
    else:
        logger.error(f'{colorama.Fore.YELLOW}Cleanup script not found: '
                     f'{cleanup_script}{colorama.Style.RESET_ALL}')
