"""SSH-based Kubernetes Cluster Deployment Script"""
# Refer to https://docs.skypilot.co/en/latest/reservations/existing-machines.html for details on how to use this script. # pylint: disable=line-too-long
import argparse
import base64
import concurrent.futures as cf
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import List, Set

import yaml

from sky.utils import ux_utils
from sky.utils.kubernetes import ssh_utils

# Colors for nicer UX
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
WARNING_YELLOW = '\x1b[33m'
NC = '\033[0m'  # No color

DEFAULT_KUBECONFIG_PATH = os.path.expanduser('~/.kube/config')
SSH_CONFIG_PATH = os.path.expanduser('~/.ssh/config')
NODE_POOLS_INFO_DIR = os.path.expanduser('~/.sky/ssh_node_pools_info')

# Get the directory of this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def parse_args():
    parser = argparse.ArgumentParser(
        description='Deploy a Kubernetes cluster on remote machines.')
    parser.add_argument(
        '--infra', help='Name of the cluster in ssh_node_pools.yaml to use')
    parser.add_argument(
        '--ssh-node-pools-file',
        dest='ssh_node_pools_file',
        default=ssh_utils.DEFAULT_SSH_NODE_POOLS_PATH,
        help=
        f'Path to SSH node pools YAML file (default: {ssh_utils.DEFAULT_SSH_NODE_POOLS_PATH})'
    )
    parser.add_argument(
        '--kubeconfig-path',
        dest='kubeconfig_path',
        default=DEFAULT_KUBECONFIG_PATH,
        help=
        f'Path to save the kubeconfig file (default: {DEFAULT_KUBECONFIG_PATH})'
    )
    parser.add_argument(
        '--use-ssh-config',
        dest='use_ssh_config',
        action='store_true',
        help='Use SSH config for host settings instead of explicit parameters')
    #TODO(romilb): The `sky local up --ips` command is deprecated and these args are now captured in the ssh_node_pools.yaml file.
    # Remove these args after 0.11.0 release.
    parser.add_argument(
        '--ips-file',
        dest='ips_file',
        help=
        '[Deprecated, use --ssh-node-pools-file instead] File containing IP addresses or SSH host entries (one per line)'
    )
    parser.add_argument(
        '--user',
        help=
        '[Deprecated, use --ssh-node-pools-file instead] Username to use for SSH (overridden by SSH config if host exists there)'
    )
    parser.add_argument(
        '--ssh-key',
        dest='ssh_key',
        help=
        '[Deprecated, use --ssh-node-pools-file instead] Path to SSH private key (overridden by SSH config if host exists there)'
    )
    parser.add_argument(
        '--context-name',
        dest='context_name',
        default='default',
        help=
        '[Deprecated, use --ssh-node-pools-file instead] Kubernetes context name'
    )
    parser.add_argument('--cleanup',
                        action='store_true',
                        help='Clean up the cluster')
    parser.add_argument(
        '--password',
        help='[Deprecated, use --ssh-node-pools-file instead] Password for sudo'
    )

    return parser.parse_args()


def run_command(cmd, shell=False):
    """Run a local command and return the output."""
    process = subprocess.run(cmd,
                             shell=shell,
                             capture_output=True,
                             text=True,
                             check=False)
    if process.returncode != 0:
        print(f'{RED}Error executing command: {cmd}{NC}')
        print(f'STDOUT: {process.stdout}')
        print(f'STDERR: {process.stderr}')
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
               use_shell=False):
    """Run a command on a remote machine via SSH."""
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

    if use_shell:
        ssh_cmd = ' '.join(ssh_cmd)

    process = subprocess.run(ssh_cmd,
                             capture_output=True,
                             text=True,
                             check=False,
                             shell=use_shell)
    if process.returncode != 0:
        print(f'{RED}Error executing command {cmd} on {node}:{NC}')
        print(f'STDERR: {process.stderr}')
        return None
    if print_output:
        print(process.stdout)
    return process.stdout.strip()


def create_askpass_script(password):
    """Create an askpass script block for sudo with password."""
    if not password:
        return ''

    return f"""
# Create temporary askpass script
ASKPASS_SCRIPT=$(mktemp)
trap 'rm -f $ASKPASS_SCRIPT' EXIT INT TERM ERR QUIT
cat > $ASKPASS_SCRIPT << EOF
#!/bin/bash
echo {password}
EOF
chmod 700 $ASKPASS_SCRIPT
# Use askpass
export SUDO_ASKPASS=$ASKPASS_SCRIPT
"""


def progress_message(message):
    """Show a progress message."""
    print(f'{YELLOW}➜ {message}{NC}')


def success_message(message):
    """Show a success message."""
    print(f'{GREEN}✔ {message}{NC}')


def cleanup_server_node(node,
                        user,
                        ssh_key,
                        askpass_block,
                        use_ssh_config=False):
    """Uninstall k3s and clean up the state on a server node."""
    print(f'{YELLOW}Cleaning up head node {node}...{NC}')
    cmd = f"""
        {askpass_block}
        echo 'Uninstalling k3s...' &&
        sudo -A /usr/local/bin/k3s-uninstall.sh || true &&
        sudo -A rm -rf /etc/rancher /var/lib/rancher /var/lib/kubelet /etc/kubernetes ~/.kube
    """
    result = run_remote(node, cmd, user, ssh_key, use_ssh_config=use_ssh_config)
    if result is None:
        print(f'{RED}Failed to clean up head node ({node}).{NC}')
    else:
        success_message(f'Node {node} cleaned up successfully.')


def cleanup_agent_node(node,
                       user,
                       ssh_key,
                       askpass_block,
                       use_ssh_config=False):
    """Uninstall k3s and clean up the state on an agent node."""
    print(f'{YELLOW}Cleaning up worker node {node}...{NC}')
    cmd = f"""
        {askpass_block}
        echo 'Uninstalling k3s...' &&
        sudo -A /usr/local/bin/k3s-agent-uninstall.sh || true &&
        sudo -A rm -rf /etc/rancher /var/lib/rancher /var/lib/kubelet /etc/kubernetes ~/.kube
    """
    result = run_remote(node, cmd, user, ssh_key, use_ssh_config=use_ssh_config)
    if result is None:
        print(f'{RED}Failed to clean up worker node ({node}).{NC}')
    else:
        success_message(f'Node {node} cleaned up successfully.')


def start_agent_node(node,
                     master_addr,
                     k3s_token,
                     user,
                     ssh_key,
                     askpass_block,
                     use_ssh_config=False):
    """Start a k3s agent node.
    Returns: if the start is successful, and if the node has a GPU."""
    cmd = f"""
            {askpass_block}
            curl -sfL https://get.k3s.io | K3S_NODE_NAME={node} INSTALL_K3S_EXEC='agent --node-label skypilot-ip={node}' \
                K3S_URL=https://{master_addr}:6443 K3S_TOKEN={k3s_token} sudo -E -A sh -
        """
    result = run_remote(node, cmd, user, ssh_key, use_ssh_config=use_ssh_config)
    if result is None:
        print(f'{RED}Failed to deploy K3s on worker node ({node}).{NC}')
        return node, False, False
    success_message(f'Kubernetes deployed on worker node ({node}).')
    # Check if worker node has a GPU
    if check_gpu(node, user, ssh_key, use_ssh_config=use_ssh_config):
        print(f'{YELLOW}GPU detected on worker node ({node}).{NC}')
        return node, True, True
    return node, True, False


def check_gpu(node, user, ssh_key, use_ssh_config=False):
    """Check if a node has a GPU."""
    cmd = 'command -v nvidia-smi &> /dev/null && nvidia-smi --query-gpu=gpu_name --format=csv,noheader &> /dev/null'
    result = run_remote(node, cmd, user, ssh_key, use_ssh_config=use_ssh_config)
    return result is not None


def ensure_directory_exists(path):
    """Ensure the directory for the specified file path exists."""
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)


def get_used_localhost_ports() -> Set[int]:
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
    used_ports = get_used_localhost_ports()

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
    progress_message('Setting up SSH tunnel for Kubernetes API access...')

    # Get an available port for this cluster
    port = get_available_port()

    # Paths to scripts
    tunnel_script = os.path.join(SCRIPT_DIR, 'ssh-tunnel.sh')

    # Make sure scripts are executable
    os.chmod(tunnel_script, 0o755)

    # Certificate files
    client_cert_file = os.path.join(NODE_POOLS_INFO_DIR,
                                    f'{context_name}-cert.pem')
    client_key_file = os.path.join(NODE_POOLS_INFO_DIR,
                                   f'{context_name}-key.pem')

    # Update kubeconfig to use localhost with the selected port
    run_command([
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
        print(
            f'{GREEN}Client certificate data extracted and will be used for authentication{NC}'
        )

    if use_ssh_config:
        run_command(
            ['kubectl', 'config', 'set-credentials', context_name] + exec_args +
            [
                '--exec-arg=--context', f'--exec-arg={context_name}',
                '--exec-arg=--port', f'--exec-arg={port}', '--exec-arg=--ttl',
                f'--exec-arg={ttl_seconds}', '--exec-arg=--use-ssh-config',
                '--exec-arg=--host', f'--exec-arg={head_node}'
            ])
    else:
        run_command(['kubectl', 'config', 'set-credentials', context_name] +
                    exec_args + [
                        '--exec-arg=--context', f'--exec-arg={context_name}',
                        '--exec-arg=--port', f'--exec-arg={port}',
                        '--exec-arg=--ttl', f'--exec-arg={ttl_seconds}',
                        '--exec-arg=--host', f'--exec-arg={head_node}',
                        '--exec-arg=--user', f'--exec-arg={ssh_user}',
                        '--exec-arg=--ssh-key', f'--exec-arg={ssh_key}'
                    ])

    success_message(
        f'SSH tunnel configured through kubectl credential plugin on port {port}'
    )
    print(
        f'{GREEN}Your kubectl connection is now tunneled through SSH (port {port}).{NC}'
    )
    print(
        f'{GREEN}This tunnel will be automatically established when needed.{NC}'
    )
    print(
        f'{GREEN}Credential TTL set to {ttl_seconds}s to ensure tunnel health is checked frequently.{NC}'
    )

    return port


def cleanup_kubectl_ssh_tunnel(context_name):
    """Clean up the SSH tunnel for a specific context"""
    progress_message(f'Cleaning up SSH tunnel for context {context_name}...')

    # Path to cleanup script
    cleanup_script = os.path.join(SCRIPT_DIR, 'cleanup-tunnel.sh')

    # Make sure script is executable
    if os.path.exists(cleanup_script):
        os.chmod(cleanup_script, 0o755)

        # Run the cleanup script
        subprocess.run([cleanup_script, context_name],
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       check=False)

        success_message(f'SSH tunnel for context {context_name} cleaned up')
    else:
        print(f'{YELLOW}Cleanup script not found: {cleanup_script}{NC}')


def main():
    args = parse_args()

    kubeconfig_path = os.path.expanduser(args.kubeconfig_path)
    global_use_ssh_config = args.use_ssh_config

    failed_clusters = []
    successful_clusters = []

    # Print cleanup mode marker if applicable
    if args.cleanup:
        print('SKYPILOT_CLEANUP_MODE: Cleanup mode activated')

    # Check if using YAML configuration or command line arguments
    if args.ips_file:
        # Using command line arguments - legacy mode
        if args.ssh_key and not os.path.isfile(
                args.ssh_key) and not global_use_ssh_config:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'SSH key not found: {args.ssh_key}')

        if not os.path.isfile(args.ips_file):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'IPs file not found: {args.ips_file}')

        with open(args.ips_file, 'r', encoding='utf-8') as f:
            hosts = [line.strip() for line in f if line.strip()]

        if not hosts:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Hosts file is empty or not formatted correctly.')

        head_node = hosts[0]
        worker_nodes = hosts[1:]
        ssh_user = args.user if not global_use_ssh_config else ''
        ssh_key = args.ssh_key if not global_use_ssh_config else ''
        context_name = args.context_name
        password = args.password

        # Check if hosts are in SSH config
        head_use_ssh_config = global_use_ssh_config or ssh_utils.check_host_in_ssh_config(
            head_node)
        worker_use_ssh_config = [
            global_use_ssh_config or ssh_utils.check_host_in_ssh_config(node)
            for node in worker_nodes
        ]

        # Single cluster deployment for legacy mode
        deploy_cluster(head_node, worker_nodes, ssh_user, ssh_key, context_name,
                       password, head_use_ssh_config, worker_use_ssh_config,
                       kubeconfig_path, args.cleanup)
    else:
        # Using YAML configuration
        targets = ssh_utils.load_ssh_targets(args.ssh_node_pools_file)
        clusters_config = ssh_utils.get_cluster_config(
            targets, args.infra, file_path=args.ssh_node_pools_file)

        # Print information about clusters being processed
        num_clusters = len(clusters_config)
        cluster_names = list(clusters_config.keys())
        cluster_info = f'Found {num_clusters} Node Pool{"s" if num_clusters > 1 else ""}: {", ".join(cluster_names)}'
        print(f'SKYPILOT_CLUSTER_INFO: {cluster_info}')

        # Process each cluster
        for cluster_name, cluster_config in clusters_config.items():
            try:
                print(f'SKYPILOT_CURRENT_CLUSTER: {cluster_name}')
                print(
                    f'{YELLOW}==== Deploying cluster: {cluster_name} ====${NC}')
                hosts_info = ssh_utils.prepare_hosts_info(
                    cluster_name, cluster_config)

                if not hosts_info:
                    print(
                        f'{RED}Error: No valid hosts found for cluster {cluster_name!r}. Skipping.{NC}'
                    )
                    continue

                # Generate a unique context name for each cluster
                context_name = args.context_name
                if context_name == 'default':
                    context_name = 'ssh-' + cluster_name

                # Check cluster history
                os.makedirs(NODE_POOLS_INFO_DIR, exist_ok=True)
                history_yaml_file = os.path.join(
                    NODE_POOLS_INFO_DIR, f'{context_name}-history.yaml')

                history = None
                if os.path.exists(history_yaml_file):
                    print(
                        f'{YELLOW}Loading history from {history_yaml_file}{NC}')
                    with open(history_yaml_file, 'r', encoding='utf-8') as f:
                        history = yaml.safe_load(f)
                else:
                    print(f'{YELLOW}No history found for {context_name}.{NC}')

                history_workers_info = None
                history_worker_nodes = None
                history_use_ssh_config = None
                # Do not support changing anything besides hosts for now
                if history is not None:
                    for key in ['user', 'identity_file', 'password']:
                        if not args.cleanup and history.get(
                                key) != cluster_config.get(key):
                            raise ValueError(
                                f'Cluster configuration has changed for field {key!r}. '
                                f'Previous value: {history.get(key)}, '
                                f'Current value: {cluster_config.get(key)}')
                    history_hosts_info = ssh_utils.prepare_hosts_info(
                        cluster_name, history)
                    if not args.cleanup and history_hosts_info[0] != hosts_info[
                            0]:
                        raise ValueError(
                            f'Cluster configuration has changed for master node. '
                            f'Previous value: {history_hosts_info[0]}, '
                            f'Current value: {hosts_info[0]}')
                    history_workers_info = history_hosts_info[1:] if len(
                        history_hosts_info) > 1 else []
                    history_worker_nodes = [
                        h['ip'] for h in history_workers_info
                    ]
                    history_use_ssh_config = [
                        h.get('use_ssh_config', False)
                        for h in history_workers_info
                    ]

                # Use the first host as the head node and the rest as worker nodes
                head_host = hosts_info[0]
                worker_hosts = hosts_info[1:] if len(hosts_info) > 1 else []

                head_node = head_host['ip']
                worker_nodes = [h['ip'] for h in worker_hosts]
                ssh_user = head_host['user']
                ssh_key = head_host['identity_file']
                head_use_ssh_config = global_use_ssh_config or head_host.get(
                    'use_ssh_config', False)
                worker_use_ssh_config = [
                    global_use_ssh_config or h.get('use_ssh_config', False)
                    for h in worker_hosts
                ]
                password = head_host['password']

                # Deploy this cluster
                unsuccessful_workers = deploy_cluster(
                    head_node,
                    worker_nodes,
                    ssh_user,
                    ssh_key,
                    context_name,
                    password,
                    head_use_ssh_config,
                    worker_use_ssh_config,
                    kubeconfig_path,
                    args.cleanup,
                    worker_hosts=worker_hosts,
                    history_worker_nodes=history_worker_nodes,
                    history_workers_info=history_workers_info,
                    history_use_ssh_config=history_use_ssh_config)

                if not args.cleanup:
                    successful_hosts = []
                    for host in cluster_config['hosts']:
                        if isinstance(host, str):
                            host_node = host
                        else:
                            host_node = host['ip']
                        if host_node not in unsuccessful_workers:
                            successful_hosts.append(host)
                    cluster_config['hosts'] = successful_hosts
                    with open(history_yaml_file, 'w', encoding='utf-8') as f:
                        print(
                            f'{YELLOW}Writing history to {history_yaml_file}{NC}'
                        )
                        yaml.dump(cluster_config, f)

                print(
                    f'{GREEN}==== Completed deployment for cluster: {cluster_name} ====${NC}'
                )
                successful_clusters.append(cluster_name)
            except Exception as e:  # pylint: disable=broad-except
                reason = str(e)
                failed_clusters.append((cluster_name, reason))
                print(
                    f'{RED}Error deploying SSH Node Pool {cluster_name}: {reason}{NC}'
                )  # Print for internal logging

    if failed_clusters:
        action = 'clean' if args.cleanup else 'deploy'
        msg = f'{GREEN}Successfully {action}ed {len(successful_clusters)} cluster(s) ({", ".join(successful_clusters)}). {NC}'
        msg += f'{RED}Failed to {action} {len(failed_clusters)} cluster(s): {NC}'
        for cluster_name, reason in failed_clusters:
            msg += f'\n  {cluster_name}: {reason}'
        raise RuntimeError(msg)


def deploy_cluster(head_node,
                   worker_nodes,
                   ssh_user,
                   ssh_key,
                   context_name,
                   password,
                   head_use_ssh_config,
                   worker_use_ssh_config,
                   kubeconfig_path,
                   cleanup,
                   worker_hosts=None,
                   history_worker_nodes=None,
                   history_workers_info=None,
                   history_use_ssh_config=None) -> List[str]:
    """Deploy or clean up a single Kubernetes cluster.

    Returns: List of unsuccessful worker nodes.
    """
    history_yaml_file = os.path.join(NODE_POOLS_INFO_DIR,
                                     f'{context_name}-history.yaml')
    cert_file_path = os.path.join(NODE_POOLS_INFO_DIR,
                                  f'{context_name}-cert.pem')
    key_file_path = os.path.join(NODE_POOLS_INFO_DIR, f'{context_name}-key.pem')
    tunnel_log_file_path = os.path.join(NODE_POOLS_INFO_DIR,
                                        f'{context_name}-tunnel.log')

    # Generate the askpass block if password is provided
    askpass_block = create_askpass_script(password)

    # Token for k3s
    k3s_token = 'mytoken'  # Any string can be used as the token

    # Pre-flight checks
    print(f'{YELLOW}Checking SSH connection to head node...{NC}')
    result = run_remote(
        head_node,
        f'echo \'SSH connection successful ({head_node})\'',
        ssh_user,
        ssh_key,
        use_ssh_config=head_use_ssh_config,
        # For SkySSHUpLineProcessor
        print_output=True)
    if not cleanup and result is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                f'Failed to SSH to head node ({head_node}). '
                f'Please check the SSH configuration and logs for more details.'
            )

    # Checking history
    history_exists = (history_worker_nodes is not None and
                      history_workers_info is not None and
                      history_use_ssh_config is not None)

    # Cleanup history worker nodes
    worker_nodes_to_cleanup = []
    remove_worker_cmds = []
    if history_exists:
        for history_node, history_info, use_ssh_config in zip(
                history_worker_nodes, history_workers_info,
                history_use_ssh_config):
            if worker_hosts is not None and history_info not in worker_hosts:
                print(
                    f'{YELLOW}Worker node {history_node} not found in YAML config. '
                    f'Removing from history...{NC}')
                worker_nodes_to_cleanup.append(
                    dict(
                        node=history_node,
                        user=ssh_user
                        if history_info is None else history_info['user'],
                        ssh_key=ssh_key if history_info is None else
                        history_info['identity_file'],
                        askpass_block=(askpass_block if history_info is None
                                       else create_askpass_script(
                                           history_info['password'])),
                        use_ssh_config=use_ssh_config,
                    ))
                remove_worker_cmds.append(
                    f'kubectl delete node -l skypilot-ip={history_node}')
        # If this is a create operation and there exists some stale log,
        # cleanup the log for a new file to store new logs.
        if not cleanup and os.path.exists(tunnel_log_file_path):
            os.remove(tunnel_log_file_path)

    # If --cleanup flag is set, uninstall k3s and exit
    if cleanup:
        # Pickup all nodes
        worker_nodes_to_cleanup.clear()
        for node, info, use_ssh_config in zip(worker_nodes, worker_hosts,
                                              worker_use_ssh_config):
            worker_nodes_to_cleanup.append(
                dict(
                    node=node,
                    user=ssh_user if info is None else info['user'],
                    ssh_key=ssh_key if info is None else info['identity_file'],
                    askpass_block=(askpass_block if info is None else
                                   create_askpass_script(info['password'])),
                    use_ssh_config=use_ssh_config,
                ))

        print(f'{YELLOW}Starting cleanup...{NC}')

        # Clean up head node
        cleanup_server_node(head_node,
                            ssh_user,
                            ssh_key,
                            askpass_block,
                            use_ssh_config=head_use_ssh_config)
    # Clean up worker nodes
    with cf.ThreadPoolExecutor() as executor:
        executor.map(lambda kwargs: cleanup_agent_node(**kwargs),
                     worker_nodes_to_cleanup)

    with cf.ThreadPoolExecutor() as executor:

        def run_cleanup_cmd(cmd):
            print('Cleaning up worker nodes:', cmd)
            run_command(cmd, shell=True)

        executor.map(run_cleanup_cmd, remove_worker_cmds)

    if cleanup:

        # Remove the context from local kubeconfig if it exists
        if os.path.isfile(kubeconfig_path):
            progress_message(
                f'Removing context {context_name!r} from local kubeconfig...')
            run_command(['kubectl', 'config', 'delete-context', context_name],
                        shell=False)
            run_command(['kubectl', 'config', 'delete-cluster', context_name],
                        shell=False)
            run_command(['kubectl', 'config', 'delete-user', context_name],
                        shell=False)

            # Update the current context to the first available context
            contexts = run_command([
                'kubectl', 'config', 'view', '-o',
                'jsonpath=\'{.contexts[0].name}\''
            ],
                                   shell=False)
            if contexts:
                run_command(['kubectl', 'config', 'use-context', contexts],
                            shell=False)
            else:
                # If no context is available, simply unset the current context
                run_command(['kubectl', 'config', 'unset', 'current-context'],
                            shell=False)

            success_message(
                f'Context {context_name!r} removed from local kubeconfig.')

        for file in [history_yaml_file, cert_file_path, key_file_path]:
            if os.path.exists(file):
                os.remove(file)

        # Clean up SSH tunnel after clean up kubeconfig, because the kubectl
        # will restart the ssh tunnel if it's not running.
        cleanup_kubectl_ssh_tunnel(context_name)

        print(f'{GREEN}Cleanup completed successfully.{NC}')

        # Print completion marker for current cluster
        print(f'{GREEN}SKYPILOT_CLUSTER_COMPLETED: {NC}')

        return []

    print(f'{YELLOW}Checking TCP Forwarding Options...{NC}')
    cmd = (
        'if [ "$(sudo sshd -T | grep allowtcpforwarding)" = "allowtcpforwarding yes" ]; then '
        f'echo "TCP Forwarding already enabled on head node ({head_node})."; '
        'else '
        'sudo sed -i \'s/^#\?\s*AllowTcpForwarding.*/AllowTcpForwarding yes/\' '  # pylint: disable=anomalous-backslash-in-string
        '/etc/ssh/sshd_config && sudo systemctl restart sshd && '
        f'echo "Successfully enabled TCP Forwarding on head node ({head_node})."; '
        'fi')
    result = run_remote(
        head_node,
        shlex.quote(cmd),
        ssh_user,
        ssh_key,
        use_ssh_config=head_use_ssh_config,
        # For SkySSHUpLineProcessor
        print_output=True,
        use_shell=True)
    if result is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                f'Failed to setup TCP forwarding on head node ({head_node}). '
                f'Please check the SSH configuration.')

    # Get effective IP for master node if using SSH config - needed for workers to connect
    if head_use_ssh_config:
        effective_master_ip = get_effective_host_ip(head_node)
        print(
            f'{GREEN}Resolved head node {head_node} to {effective_master_ip} from SSH config{NC}'
        )
    else:
        effective_master_ip = head_node

    # Step 1: Install k3s on the head node
    # Check if head node has a GPU
    install_gpu = False
    progress_message(f'Deploying Kubernetes on head node ({head_node})...')
    cmd = f"""
        {askpass_block}
        curl -sfL https://get.k3s.io | K3S_TOKEN={k3s_token} K3S_NODE_NAME={head_node} sudo -E -A sh - &&
        mkdir -p ~/.kube &&
        sudo -A cp /etc/rancher/k3s/k3s.yaml ~/.kube/config &&
        sudo -A chown $(id -u):$(id -g) ~/.kube/config &&
        for i in {{1..3}}; do
            if kubectl wait --for=condition=ready node --all --timeout=2m --kubeconfig ~/.kube/config; then
                break
            else
                echo 'Waiting for nodes to be ready...'
                sleep 5
            fi
        done
        if [ $i -eq 3 ]; then
            echo 'Failed to wait for nodes to be ready after 3 attempts'
            exit 1
        fi
    """
    result = run_remote(head_node,
                        cmd,
                        ssh_user,
                        ssh_key,
                        use_ssh_config=head_use_ssh_config)
    if result is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                f'Failed to deploy K3s on head node ({head_node}).')
    success_message(f'K3s deployed on head node ({head_node}).')

    # Check if head node has a GPU
    install_gpu = False
    if check_gpu(head_node,
                 ssh_user,
                 ssh_key,
                 use_ssh_config=head_use_ssh_config):
        print(f'{YELLOW}GPU detected on head node ({head_node}).{NC}')
        install_gpu = True

    # Fetch the head node's internal IP (this will be passed to worker nodes)
    master_addr = run_remote(head_node,
                             'hostname -I | awk \'{print $1}\'',
                             ssh_user,
                             ssh_key,
                             use_ssh_config=head_use_ssh_config)
    if master_addr is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(f'Failed to SSH to head node ({head_node}). '
                               f'Please check the SSH configuration.')
    print(f'{GREEN}Master node internal IP: {master_addr}{NC}')

    # Step 2: Install k3s on worker nodes and join them to the master node
    def deploy_worker(args):
        (i, node, worker_hosts, history_workers_info, ssh_user, ssh_key,
         askpass_block, worker_use_ssh_config, master_addr, k3s_token) = args
        progress_message(f'Deploying Kubernetes on worker node ({node})...')

        # If using YAML config with specific worker info
        if worker_hosts and i < len(worker_hosts):
            if history_workers_info is not None and worker_hosts[
                    i] in history_workers_info:
                print(
                    f'{YELLOW}Worker node ({node}) already exists in history. '
                    f'Skipping...{NC}')
                return node, True, False
            worker_user = worker_hosts[i]['user']
            worker_key = worker_hosts[i]['identity_file']
            worker_password = worker_hosts[i]['password']
            worker_askpass = create_askpass_script(worker_password)
            worker_config = worker_use_ssh_config[i]
        else:
            worker_user = ssh_user
            worker_key = ssh_key
            worker_askpass = askpass_block
            worker_config = worker_use_ssh_config[i]

        return start_agent_node(node,
                                master_addr,
                                k3s_token,
                                worker_user,
                                worker_key,
                                worker_askpass,
                                use_ssh_config=worker_config)

    unsuccessful_workers = []

    # Deploy workers in parallel using thread pool
    with cf.ThreadPoolExecutor() as executor:
        futures = []
        for i, node in enumerate(worker_nodes):
            args = (i, node, worker_hosts, history_workers_info, ssh_user,
                    ssh_key, askpass_block, worker_use_ssh_config, master_addr,
                    k3s_token)
            futures.append(executor.submit(deploy_worker, args))

        # Check if worker node has a GPU
        for future in cf.as_completed(futures):
            node, suc, has_gpu = future.result()
            install_gpu = install_gpu or has_gpu
            if not suc:
                unsuccessful_workers.append(node)

    # Step 3: Configure local kubectl to connect to the cluster
    progress_message('Configuring local kubectl to connect to the cluster...')

    # Create temporary directory for kubeconfig operations
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_kubeconfig = os.path.join(temp_dir, 'kubeconfig')

        # Get the kubeconfig from remote server
        if head_use_ssh_config:
            scp_cmd = ['scp', head_node + ':~/.kube/config', temp_kubeconfig]
        else:
            scp_cmd = [
                'scp', '-o', 'StrictHostKeyChecking=no', '-o',
                'IdentitiesOnly=yes', '-i', ssh_key,
                f'{ssh_user}@{head_node}:~/.kube/config', temp_kubeconfig
            ]
        run_command(scp_cmd, shell=False)

        # Create the directory for the kubeconfig file if it doesn't exist
        ensure_directory_exists(kubeconfig_path)

        # Create empty kubeconfig if it doesn't exist
        if not os.path.isfile(kubeconfig_path):
            open(kubeconfig_path, 'a', encoding='utf-8').close()

        # Modify the temporary kubeconfig to update server address and context name
        modified_config = os.path.join(temp_dir, 'modified_config')
        with open(temp_kubeconfig, 'r', encoding='utf-8') as f_in:
            with open(modified_config, 'w', encoding='utf-8') as f_out:
                in_cluster = False
                in_user = False
                client_cert_data = None
                client_key_data = None

                for line in f_in:
                    if 'clusters:' in line:
                        in_cluster = True
                        in_user = False
                    elif 'users:' in line:
                        in_cluster = False
                        in_user = True
                    elif 'contexts:' in line:
                        in_cluster = False
                        in_user = False

                    # Skip certificate authority data in cluster section
                    if in_cluster and 'certificate-authority-data:' in line:
                        continue
                    # Skip client certificate data in user section but extract it
                    elif in_user and 'client-certificate-data:' in line:
                        client_cert_data = line.split(':', 1)[1].strip()
                        continue
                    # Skip client key data in user section but extract it
                    elif in_user and 'client-key-data:' in line:
                        client_key_data = line.split(':', 1)[1].strip()
                        continue
                    elif in_cluster and 'server:' in line:
                        # Initially just set to the effective master IP
                        # (will be changed to localhost by setup_kubectl_ssh_tunnel later)
                        f_out.write(
                            f'    server: https://{effective_master_ip}:6443\n')
                        f_out.write('    insecure-skip-tls-verify: true\n')
                        continue

                    # Replace default context names with user-provided context name
                    line = line.replace('name: default',
                                        f'name: {context_name}')
                    line = line.replace('cluster: default',
                                        f'cluster: {context_name}')
                    line = line.replace('user: default',
                                        f'user: {context_name}')
                    line = line.replace('current-context: default',
                                        f'current-context: {context_name}')

                    f_out.write(line)

                # Save certificate data if available

                if client_cert_data:
                    # Decode base64 data and save as PEM
                    try:
                        # Clean up the certificate data by removing whitespace
                        clean_cert_data = ''.join(client_cert_data.split())
                        cert_pem = base64.b64decode(clean_cert_data).decode(
                            'utf-8')

                        # Check if the data already looks like a PEM file
                        has_begin = '-----BEGIN CERTIFICATE-----' in cert_pem
                        has_end = '-----END CERTIFICATE-----' in cert_pem

                        if not has_begin or not has_end:
                            print(
                                f'{YELLOW}Warning: Certificate data missing PEM markers, attempting to fix...{NC}'
                            )
                            # Add PEM markers if missing
                            if not has_begin:
                                cert_pem = f'-----BEGIN CERTIFICATE-----\n{cert_pem}'
                            if not has_end:
                                cert_pem = f'{cert_pem}\n-----END CERTIFICATE-----'

                        # Write the certificate
                        with open(cert_file_path, 'w',
                                  encoding='utf-8') as cert_file:
                            cert_file.write(cert_pem)

                        # Verify the file was written correctly
                        if os.path.getsize(cert_file_path) > 0:
                            print(
                                f'{GREEN}Successfully saved certificate data ({len(cert_pem)} bytes){NC}'
                            )

                            # Quick validation of PEM format
                            with open(cert_file_path, 'r',
                                      encoding='utf-8') as f:
                                content = f.readlines()
                                first_line = content[0].strip(
                                ) if content else ''
                                last_line = content[-1].strip(
                                ) if content else ''

                            if not first_line.startswith(
                                    '-----BEGIN') or not last_line.startswith(
                                        '-----END'):
                                print(
                                    f'{YELLOW}Warning: Certificate may not be in proper PEM format{NC}'
                                )
                        else:
                            print(f'{RED}Error: Certificate file is empty{NC}')
                    except Exception as e:  # pylint: disable=broad-except
                        print(
                            f'{RED}Error processing certificate data: {e}{NC}')

                if client_key_data:
                    # Decode base64 data and save as PEM
                    try:
                        # Clean up the key data by removing whitespace
                        clean_key_data = ''.join(client_key_data.split())
                        key_pem = base64.b64decode(clean_key_data).decode(
                            'utf-8')

                        # Check if the data already looks like a PEM file

                        # Check for EC key format
                        if 'EC PRIVATE KEY' in key_pem:
                            # Handle EC KEY format directly
                            match_ec = re.search(
                                r'-----BEGIN EC PRIVATE KEY-----(.*?)-----END EC PRIVATE KEY-----',
                                key_pem, re.DOTALL)
                            if match_ec:
                                # Extract and properly format EC key
                                key_content = match_ec.group(1).strip()
                                key_pem = f'-----BEGIN EC PRIVATE KEY-----\n{key_content}\n-----END EC PRIVATE KEY-----'
                            else:
                                # Extract content and assume EC format
                                key_content = re.sub(r'-----BEGIN.*?-----', '',
                                                     key_pem)
                                key_content = re.sub(r'-----END.*?-----.*', '',
                                                     key_content).strip()
                                key_pem = f'-----BEGIN EC PRIVATE KEY-----\n{key_content}\n-----END EC PRIVATE KEY-----'
                        else:
                            # Handle regular private key format
                            has_begin = any(marker in key_pem for marker in [
                                '-----BEGIN PRIVATE KEY-----',
                                '-----BEGIN RSA PRIVATE KEY-----'
                            ])
                            has_end = any(marker in key_pem for marker in [
                                '-----END PRIVATE KEY-----',
                                '-----END RSA PRIVATE KEY-----'
                            ])

                            if not has_begin or not has_end:
                                print(
                                    f'{YELLOW}Warning: Key data missing PEM markers, attempting to fix...{NC}'
                                )
                                # Add PEM markers if missing
                                if not has_begin:
                                    key_pem = f'-----BEGIN PRIVATE KEY-----\n{key_pem}'
                                if not has_end:
                                    key_pem = f'{key_pem}\n-----END PRIVATE KEY-----'
                                    # Remove any trailing characters after END marker
                                    key_pem = re.sub(
                                        r'(-----END PRIVATE KEY-----).*', r'\1',
                                        key_pem)

                        # Write the key
                        with open(key_file_path, 'w',
                                  encoding='utf-8') as key_file:
                            key_file.write(key_pem)

                        # Verify the file was written correctly
                        if os.path.getsize(key_file_path) > 0:
                            print(
                                f'{GREEN}Successfully saved key data ({len(key_pem)} bytes){NC}'
                            )

                            # Quick validation of PEM format
                            with open(key_file_path, 'r',
                                      encoding='utf-8') as f:
                                content = f.readlines()
                                first_line = content[0].strip(
                                ) if content else ''
                                last_line = content[-1].strip(
                                ) if content else ''

                            if not first_line.startswith(
                                    '-----BEGIN') or not last_line.startswith(
                                        '-----END'):
                                print(
                                    f'{YELLOW}Warning: Key may not be in proper PEM format{NC}'
                                )
                        else:
                            print(f'{RED}Error: Key file is empty{NC}')
                    except Exception as e:  # pylint: disable=broad-except
                        print(f'{RED}Error processing key data: {e}{NC}')

        # First check if context name exists and delete it if it does
        # TODO(romilb): Should we throw an error here instead?
        run_command(['kubectl', 'config', 'delete-context', context_name],
                    shell=False)
        run_command(['kubectl', 'config', 'delete-cluster', context_name],
                    shell=False)
        run_command(['kubectl', 'config', 'delete-user', context_name],
                    shell=False)

        # Merge the configurations using kubectl
        merged_config = os.path.join(temp_dir, 'merged_config')
        os.environ['KUBECONFIG'] = f'{kubeconfig_path}:{modified_config}'
        with open(merged_config, 'w', encoding='utf-8') as merged_file:
            kubectl_cmd = ['kubectl', 'config', 'view', '--flatten']
            result = run_command(kubectl_cmd, shell=False)
            if result:
                merged_file.write(result)

        # Replace the kubeconfig with the merged config
        shutil.move(merged_config, kubeconfig_path)

        # Set the new context as the current context
        run_command(['kubectl', 'config', 'use-context', context_name],
                    shell=False)

    # Always set up SSH tunnel since we assume only port 22 is accessible
    setup_kubectl_ssh_tunnel(head_node,
                             ssh_user,
                             ssh_key,
                             context_name,
                             use_ssh_config=head_use_ssh_config)

    success_message(f'kubectl configured with new context \'{context_name}\'.')

    print(
        f'Cluster deployment completed. Kubeconfig saved to {kubeconfig_path}')
    print('You can now run \'kubectl get nodes\' to verify the setup.')

    # Install GPU operator if a GPU was detected on any node
    if install_gpu:
        print(
            f'{YELLOW}GPU detected in the cluster. Installing Nvidia GPU Operator...{NC}'
        )
        cmd = f"""
            {askpass_block}
            curl -fsSL -o get_helm.sh https://raw.githubusercontent.com/helm/helm/master/scripts/get-helm-3 &&
            chmod 700 get_helm.sh &&
            ./get_helm.sh &&
            helm repo add nvidia https://helm.ngc.nvidia.com/nvidia && helm repo update &&
            kubectl create namespace gpu-operator --kubeconfig ~/.kube/config || true &&
            sudo -A ln -s /sbin/ldconfig /sbin/ldconfig.real || true &&
            helm install gpu-operator -n gpu-operator --create-namespace nvidia/gpu-operator \\
            --set 'toolkit.env[0].name=CONTAINERD_CONFIG' \\
            --set 'toolkit.env[0].value=/var/lib/rancher/k3s/agent/etc/containerd/config.toml' \\
            --set 'toolkit.env[1].name=CONTAINERD_SOCKET' \\
            --set 'toolkit.env[1].value=/run/k3s/containerd/containerd.sock' \\
            --set 'toolkit.env[2].name=CONTAINERD_RUNTIME_CLASS' \\
            --set 'toolkit.env[2].value=nvidia' &&
            echo 'Waiting for GPU operator installation...' &&
            while ! kubectl describe nodes --kubeconfig ~/.kube/config | grep -q 'nvidia.com/gpu:' || ! kubectl describe nodes --kubeconfig ~/.kube/config | grep -q 'nvidia.com/gpu.product'; do
                echo 'Waiting for GPU operator...'
                sleep 5
            done 
            echo 'GPU operator installed successfully.'
        """
        result = run_remote(head_node,
                            cmd,
                            ssh_user,
                            ssh_key,
                            use_ssh_config=head_use_ssh_config)
        if result is None:
            print(f'{RED}Failed to install GPU Operator.{NC}')
        else:
            success_message('GPU Operator installed.')
    else:
        print(
            f'{YELLOW}No GPUs detected. Skipping GPU Operator installation.{NC}'
        )

    # Configure SkyPilot
    progress_message('Configuring SkyPilot...')

    # The env var KUBECONFIG ensures sky check uses the right kubeconfig
    os.environ['KUBECONFIG'] = kubeconfig_path
    run_command(['sky', 'check', 'kubernetes'], shell=False)

    success_message('SkyPilot configured successfully.')

    # Display final success message
    print(
        f'{GREEN}==== 🎉 Kubernetes cluster deployment completed successfully 🎉 ====${NC}'
    )
    print(
        'You can now interact with your Kubernetes cluster through SkyPilot: ')
    print('  • List available GPUs: sky show-gpus --cloud kubernetes')
    print(
        '  • Launch a GPU development pod: sky launch -c devbox --cloud kubernetes'
    )
    print(
        '  • Connect to pod with VSCode: code --remote ssh-remote+devbox "/home"'
    )
    # Print completion marker for current cluster
    print(f'{GREEN}SKYPILOT_CLUSTER_COMPLETED: {NC}')

    if unsuccessful_workers:
        quoted_unsuccessful_workers = [
            f'"{worker}"' for worker in unsuccessful_workers
        ]

        print(
            f'{WARNING_YELLOW}Failed to deploy Kubernetes on the following nodes: '
            f'{", ".join(quoted_unsuccessful_workers)}. Please check '
            f'the logs for more details.{NC}')

    return unsuccessful_workers


if __name__ == '__main__':
    main()
