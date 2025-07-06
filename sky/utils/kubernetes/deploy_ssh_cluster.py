"""SSH-based Kubernetes Cluster Deployment"""
# This is the python native function call method of creating an SSH Node Pool
import concurrent.futures as cf
import base64
import subprocess
import sys
import shlex
import shutil
import re
import random
import os
import tempfile
from typing import Set, Optional, Tuple

from sky import sky_logging
from sky.ssh_node_pools import constants as ssh_constants
from sky.ssh_node_pools import models as ssh_models
from sky.ssh_node_pools import state as ssh_state
from sky.ssh_node_pools.models import SSHClusterStatus
from sky.utils import ux_utils
from sky.utils.kubernetes import kubernetes_deploy_utils

# Colors for nicer UX
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
WARNING_YELLOW = '\x1b[33m'
NC = '\033[0m'  # No color

# Get the directory of this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_KUBECONFIG_PATH = os.path.expanduser('~/.kube/config')
K3S_TOKEN = 'mytoken'

logger = sky_logging.init_logger(__name__)


def run_command(cmd, shell=False):
    """Run a local command and return the output."""
    process = subprocess.run(cmd,
                             shell=shell,
                             capture_output=True,
                             text=True,
                             check=False)
    if process.returncode != 0:
        logger.error(f'{RED}Error executing command: {cmd}{NC}')
        logger.error(f'STDOUT: {process.stdout}')
        logger.error(f'STDERR: {process.stderr}')
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
        if not silent:
            logger.error(f'{RED}Error executing command {cmd} on {node}:{NC}')
            logger.error(f'STDERR: {process.stderr}')
        return None
    if print_output:
        logger.info(process.stdout)
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
    logger.info(f'{YELLOW}➜ {message}{NC}')


def success_message(message):
    """Show a success message."""
    logger.info(f'{GREEN}✔ {message}{NC}')


def cleanup_server_node(node,
                        user,
                        ssh_key,
                        askpass_block,
                        use_ssh_config=False):
    """Uninstall k3s and clean up the state on a server node."""
    logger.info(f'{YELLOW}Cleaning up head node {node}...{NC}')
    cmd = f"""
        {askpass_block}
        echo 'Uninstalling k3s...' &&
        sudo -A /usr/local/bin/k3s-uninstall.sh || true &&
        sudo -A rm -rf /etc/rancher /var/lib/rancher /var/lib/kubelet /etc/kubernetes ~/.kube
    """
    result = run_remote(node, cmd, user, ssh_key, use_ssh_config=use_ssh_config)
    if result is None:
        logger.error(f'{RED}Failed to clean up head node ({node}).{NC}')
    else:
        success_message(f'Node {node} cleaned up successfully.')


def cleanup_agent_node(node,
                       user,
                       ssh_key,
                       askpass_block,
                       use_ssh_config=False):
    """Uninstall k3s and clean up the state on an agent node."""
    logger.info(f'{YELLOW}Cleaning up worker node {node}...{NC}')
    cmd = f"""
        {askpass_block}
        echo 'Uninstalling k3s...' &&
        sudo -A /usr/local/bin/k3s-agent-uninstall.sh || true &&
        sudo -A rm -rf /etc/rancher /var/lib/rancher /var/lib/kubelet /etc/kubernetes ~/.kube
    """
    result = run_remote(node, cmd, user, ssh_key, use_ssh_config=use_ssh_config)
    if result is None:
        logger.error(f'{RED}Failed to clean up worker node ({node}).{NC}')
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
        logger.error(f'{RED}Failed to deploy K3s on worker node ({node}).{NC}')
        return node, False, False
    success_message(f'Kubernetes deployed on worker node ({node}).')
    # Check if worker node has a GPU
    if check_gpu(node, user, ssh_key, use_ssh_config=use_ssh_config):
        logger.info(f'{YELLOW}GPU detected on worker node ({node}).{NC}')
        return node, True, True
    return node, True, False


def check_gpu(node, user, ssh_key, use_ssh_config=False):
    """Check if a node has a GPU."""
    cmd = 'command -v nvidia-smi &> /dev/null && nvidia-smi --query-gpu=gpu_name --format=csv,noheader &> /dev/null'
    result = run_remote(node, cmd, user, ssh_key, use_ssh_config=use_ssh_config, silent=True)
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
    client_cert_file = os.path.join(ssh_constants.SKYSSH_METADATA_DIR,
                                    f'{context_name}-cert.pem')
    client_key_file = os.path.join(ssh_constants.SKYSSH_METADATA_DIR,
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
        logger.info(
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
    logger.info(
        f'{GREEN}Your kubectl connection is now tunneled through SSH (port {port}).{NC}'
    )
    logger.info(
        f'{GREEN}This tunnel will be automatically established when needed.{NC}'
    )
    logger.info(
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
        logger.warning(f'{YELLOW}Cleanup script not found: {cleanup_script}{NC}')


def deploy_cluster(cleanup: bool = False,
                   infra: Optional[str] = None,
                   kubeconfig_path: Optional[str] = None) -> Tuple[bool, str]:
    """Deploy a Kubernetes cluster on SSH targets.

    This function deploys a Kubernetes (k3s) cluster on the specified machines.

    Args:
        cleanup: Whether to clean up the cluster instead of deploying.
        infra: Name of the cluster to use. If None, all clusters will be used.
            Must not be None for deployment
        kubeconfig_path: Path to save the Kubernetes configuration file.
            If None, the default ~/.kube/config will be used.
    """
    assert cleanup or infra is not None

    # check requirements
    kubernetes_deploy_utils.check_ssh_cluster_dependencies()

    # get kubeconfig paths
    if not kubeconfig_path:
        kubeconfig_path = DEFAULT_KUBECONFIG_PATH
    else:
        kubeconfig_path = os.path.expanduser(kubeconfig_path)

    clusters_config = ssh_state.get_one_or_all_clusters(infra)

    failed_clusters = []
    successful_clusters = []

    # Process each cluster
    for ssh_cluster in clusters_config:
        cluster_name = ssh_cluster.name
        try:
            status = ssh_cluster.status
            action = 'Cleaning up' if cleanup else 'Deploying'
            logger.info(f'{YELLOW}==== {action} cluster: '
                        f'{cluster_name} ===={NC}')

            if cleanup:
                if not ssh_cluster.current_nodes:
                    logger.warning(f'{RED}Error: No previous cluster '
                                   f'configurations found for {cluster_name!r}'
                                   f'. Skipping...{NC}')
                    continue
                if (status != SSHClusterStatus.ACTIVE or
                    status != SSHClusterStatus.PENDING):
                    # might have an update cluster status, but it is not being started.
                    logger.warning(f'{YELLOW}Skipping clean up of non-active '
                                   f'cluster {cluster_name!r}. Current '
                                   f'cluster status: {status.name}.{NC}')
                    continue
                ssh_state.update_cluster_status(ssh_cluster, SSHClusterStatus.TERMINATING)
                status = SSHClusterStatus.TERMINATING
            else:
                if not ssh_cluster.update_nodes:
                    reason = 'no configurations set for update.'
                    failed_clusters.append((cluster_name, reason))
                    continue
                if status != SSHClusterStatus.PENDING:
                    reason = f'not a pending cluster. Current status: {status.name}'
                    failed_clusters.append((cluster_name, reason))
                    continue
                ssh_state.update_cluster_status(ssh_cluster, SSHClusterStatus.STARTING)
                status = SSHClusterStatus.STARTING

            unsuccessful_workers = _deploy_internal(ssh_cluster,
                                                    kubeconfig_path,
                                                    cleanup)

            if not cleanup:
                successful_nodes = []
                for node in ssh_cluster.update_nodes:
                    if node.ip not in unsuccessful_workers:
                        successful_nodes.append(node)
                ssh_cluster.status = SSHClusterStatus.ACTIVE
                ssh_cluster.set_update_nodes(successful_nodes)
                ssh_cluster.commit()
                ssh_state.add_or_update_cluster(ssh_cluster)
            else:
                ssh_state.update_cluster_status(
                    ssh_cluster, SSHClusterStatus.TERMINATED)

            action = 'clean up' if cleanup else 'deployment'
            success_message(f'==== Completed {action} for '
                            f'cluster: {cluster_name} ====')
            successful_clusters.append(cluster_name)

        except Exception as e: # pylint: disable=broad-except
            failed_clusters.append((cluster_name, str(e)))

    if failed_clusters:
        action = 'clean' if cleanup else 'deploy'
        msg = (f'{GREEN}Successfully {action}ed {len(successful_clusters)} '
               f'cluster(s) ({", ".join(successful_clusters)}). {NC}')
        msg += (f'{RED}Failed to {action} {len(failed_clusters)} '
                f'cluster(s): ({", ".join(failed_clusters)}){NC}')
        for cluster_name, reason in failed_clusters:
            msg += f'\n  {cluster_name}: {reason}'
        return False, msg
    return True, ''


def _deploy_internal(ssh_cluster: ssh_models.SSHCluster,
                     kubeconfig_path: str,
                     cleanup: bool) -> Set[str]:
    """Deploy or clean up a single Kubernetes cluster.

    Returns: Set of unsuccessful worker ips.
    """
    # Do not support anything besides changing hosts.
    if not cleanup:
        # TODO(kyuds): IP registry checks + other config validations
        pass

    # Check cluster ip
    context_name = f'ssh-{ssh_cluster.name}'

    head_node = ssh_cluster.get_update_head_node()
    worker_nodes = ssh_cluster.get_update_worker_nodes()

    # Some files
    cert_file_path = os.path.expanduser(os.path.join(
        ssh_constants.SKYSSH_METADATA_DIR, f'{context_name}-cert.pem'))
    key_file_path = os.path.expanduser(os.path.join(
        ssh_constants.SKYSSH_METADATA_DIR, f'{context_name}-key.pem'))
    tunnel_log_file_path = os.path.expanduser(os.path.join(
        ssh_constants.SKYSSH_METADATA_DIR, f'{context_name}-tunnel.log'))

    # Generate the askpass block if password is provided
    askpass_block = create_askpass_script(head_node.password)

    # Pre-flight checks
    logger.info(f'{YELLOW}Checking SSH connection to head node...{NC}')
    result = run_remote(
        head_node.ip,
        f'echo \'SSH connection successful ({head_node.ip})\'',
        head_node.user,
        head_node.identity_file,
        use_ssh_config=head_node.use_ssh_config,
        print_output=True)
    if not cleanup and result is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                f'Failed to SSH to head node ({head_node.ip}). Please '
                f'check the SSH configuration and logs for more details.')
    
    # Prepare node cleanup
    worker_nodes_to_cleanup = []
    remove_worker_cmds = []

    if not cleanup:
        # Clean up nodes in current state (but not in update)
        update_ips = set(ssh_cluster.get_update_ips())
        for node in ssh_cluster.current_nodes:
            if node.ip not in update_ips:
                logger.info(f'{YELLOW}Worker node {node.ip} not found in YAML '
                            f'config. Removing from history...{NC}')
                worker_nodes_to_cleanup.append(node)
                remove_worker_cmds.append(
                    f'kubectl delete node -l skypilot-ip={node.ip}')
        # Cleanup the log for a new file to store new logs.
        if os.path.exists(tunnel_log_file_path):
            os.remove(tunnel_log_file_path)
    else:
        # Clean up entire cluster
        worker_nodes_to_cleanup.extend(ssh_cluster.get_current_worker_nodes())

        # Clean up head node
        cleanup_server_node(head_node.ip,
                            head_node.user,
                            head_node.identity_file,
                            askpass_block,
                            use_ssh_config=head_node.use_ssh_config)

    # Clean up worker nodes
    with cf.ThreadPoolExecutor() as executor:
        executor.map(lambda kwargs: cleanup_agent_node(**kwargs),
                     worker_nodes_to_cleanup)

    with cf.ThreadPoolExecutor() as executor:
        def run_cleanup_cmd(cmd):
            logger.info('Cleaning up worker nodes:', cmd)
            run_command(cmd, shell=True)
        executor.map(run_cleanup_cmd, remove_worker_cmds)

    # Actual cleanup
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
            ], shell=False)

            if contexts:
                run_command(['kubectl', 'config', 'use-context', contexts],
                            shell=False)
            else:
                # If no context is available, simply unset the current context
                run_command(['kubectl', 'config', 'unset', 'current-context'],
                            shell=False)

            success_message(
                f'Context {context_name!r} removed from local kubeconfig.')

        for file in [cert_file_path, key_file_path]:
            if os.path.exists(file):
                os.remove(file)

        # Clean up SSH tunnel after clean up kubeconfig, because the kubectl
        # will restart the ssh tunnel if it's not running.
        cleanup_kubectl_ssh_tunnel(context_name)

        # Remove from DB
        ssh_state.remove_cluster(ssh_cluster.name)
        logger.info(f'{GREEN}Cleanup completed successfully `{ssh_cluster.name}`.{NC}')
        return []

    logger.info(f'{YELLOW}Checking TCP Forwarding Options...{NC}')
    cmd = (
        'if [ "$(sudo sshd -T | grep allowtcpforwarding)" = "allowtcpforwarding yes" ]; then '
        f'echo "TCP Forwarding already enabled on head node ({head_node.ip})."; '
        'else '
        'sudo sed -i \'s/^#\?\s*AllowTcpForwarding.*/AllowTcpForwarding yes/\' '  # pylint: disable=anomalous-backslash-in-string
        '/etc/ssh/sshd_config && sudo systemctl restart sshd && '
        f'echo "Successfully enabled TCP Forwarding on head node ({head_node.ip})."; '
        'fi')
    result = run_remote(
        head_node.ip,
        shlex.quote(cmd),
        head_node.user,
        head_node.identity_file,
        use_ssh_config=head_node.use_ssh_config,
        # For SkySSHUpLineProcessor
        print_output=True,
        use_shell=True)
    if result is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                f'Failed to setup TCP forwarding on head node ({head_node.ip}). '
                f'Please check the SSH configuration.')

    # Get effective IP for master node if using SSH config - needed for workers to connect
    if head_node.use_ssh_config:
        effective_master_ip = get_effective_host_ip(head_node.ip)
        logger.info(f'{GREEN}Resolved head node {head_node.ip} to '
                    f'{effective_master_ip} from SSH config{NC}')
    else:
        effective_master_ip = head_node.ip

    # Step 1: Install k3s on the head node
    # Check if head node has a GPU
    install_gpu = False
    progress_message(f'Deploying Kubernetes on head node ({head_node.ip})...')
    cmd = f"""
        {askpass_block}
        curl -sfL https://get.k3s.io | K3S_TOKEN={K3S_TOKEN} K3S_NODE_NAME={head_node.ip} sudo -E -A sh - &&
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
    result = run_remote(head_node.ip,
                        cmd,
                        head_node.user,
                        head_node.identity_file,
                        use_ssh_config=head_node.use_ssh_config)
    if result is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                f'Failed to deploy K3s on head node ({head_node.ip}).')
    success_message(f'K3s deployed on head node ({head_node.ip}).')

    # Check if head node has a GPU
    install_gpu = False
    if check_gpu(head_node.ip,
                 head_node.user,
                 head_node.identity_file,
                 use_ssh_config=head_node.use_ssh_config):
        logger.info(f'{YELLOW}GPU detected on head node ({head_node}.ip).{NC}')
        install_gpu = True

    # Fetch the head node's internal IP (this will be passed to worker nodes)
    master_addr = run_remote(head_node.ip,
                             'hostname -I | awk \'{print $1}\'',
                             head_node.user,
                             head_node.identity_file,
                             use_ssh_config=head_node.use_ssh_config)
    if master_addr is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(f'Failed to SSH to head node ({head_node}). '
                               f'Please check the SSH configuration.')
    logger.info(f'{GREEN}Master node internal IP: {master_addr}{NC}')

    # Step 2: Install k3s on worker nodes and join them to the master node
    def deploy_worker(args):
        (ssh_node, master_addr, deploy) = args
        if not deploy:
            logger.info(f'{YELLOW}Worker node ({ssh_node.ip}) already '
                        f'exists in history. Skipping...{NC}')
            return node, True, False
        return start_agent_node(ssh_node.ip,
                                master_addr,
                                K3S_TOKEN,
                                ssh_node.user,
                                ssh_node.identity_file,
                                create_askpass_script(ssh_node.password),
                                use_ssh_config=ssh_node.use_ssh_config)

    unsuccessful_workers = []

    # Deploy workers in parallel using thread pool
    with cf.ThreadPoolExecutor() as executor:
        futures = []
        current_ips = set(ssh_cluster.get_current_ips())
        for node in worker_nodes:
            args = (node, master_addr, node.ip not in current_ips)
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
        if head_node.use_ssh_config:
            scp_cmd = ['scp', head_node + ':~/.kube/config', temp_kubeconfig]
        else:
            scp_cmd = [
                'scp', '-o', 'StrictHostKeyChecking=no', '-o',
                'IdentitiesOnly=yes', '-i', head_node.identity_file,
                f'{head_node.user}@{head_node.ip}:~/.kube/config', temp_kubeconfig
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
                            logger.warning(
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
                            logger.info(
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
                                logger.warning(
                                    f'{YELLOW}Warning: Certificate may not be in proper PEM format{NC}'
                                )
                        else:
                            logger.error(f'{RED}Error: Certificate file is empty{NC}')
                    except Exception as e:  # pylint: disable=broad-except
                        logger.error(
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
                                logger.warning(
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
                            logger.info(
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
                                logger.warning(
                                    f'{YELLOW}Warning: Key may not be in proper PEM format{NC}'
                                )
                        else:
                            logger.error(f'{RED}Error: Key file is empty{NC}')
                    except Exception as e:  # pylint: disable=broad-except
                        logger.error(f'{RED}Error processing key data: {e}{NC}')

        # First check if context name exists and delete it if it does
        # TODO(romilb): Should we throw an error here instead?
        # TODO(kyuds): Should we move context check way up and
        # error out before setting up on any node?
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
    setup_kubectl_ssh_tunnel(head_node.ip,
                             head_node.user,
                             head_node.identity_file,
                             context_name,
                             use_ssh_config=head_node.use_ssh_config)

    success_message(f'kubectl configured with new context \'{context_name}\'.')

    logger.info(
        f'Cluster deployment completed. Kubeconfig saved to {kubeconfig_path}')
    logger.info('You can now run \'kubectl get nodes\' to verify the setup.')

    # Install GPU operator if a GPU was detected on any node
    if install_gpu:
        logger.info(
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
        result = run_remote(head_node.ip,
                            cmd,
                            head_node.user,
                            head_node.identity_file,
                            use_ssh_config=head_node.use_ssh_config)
        if result is None:
            logger.error(f'{RED}Failed to install GPU Operator.{NC}')
        else:
            success_message('GPU Operator installed.')
    else:
        logger.info(
            f'{YELLOW}No GPUs detected. Skipping GPU Operator installation.{NC}'
        )

    # Configure SkyPilot
    progress_message('Configuring SkyPilot...')

    # The env var KUBECONFIG ensures sky check uses the right kubeconfig
    os.environ['KUBECONFIG'] = kubeconfig_path
    run_command(['sky', 'check', 'kubernetes'], shell=False)

    success_message('SkyPilot configured successfully.')

    # Display final success message
    logger.info(
        f'{GREEN}==== 🎉 Kubernetes cluster deployment completed successfully 🎉 ====${NC}'
    )
    logger.info(
        'You can now interact with your Kubernetes cluster through SkyPilot: ')
    logger.info('  • List available GPUs: sky show-gpus --cloud kubernetes')
    logger.info(
        '  • Launch a GPU development pod: sky launch -c devbox --cloud kubernetes'
    )
    logger.info('  • Connect to pod with VSCode: code --remote ssh-remote+devbox ')

    if unsuccessful_workers:
        quoted_unsuccessful_workers = [
            f'"{worker}"' for worker in unsuccessful_workers
        ]

        logger.warning(
            f'{WARNING_YELLOW}Failed to deploy Kubernetes on the following nodes: '
            f'{", ".join(quoted_unsuccessful_workers)}. Please check '
            f'the logs for more details.{NC}')

    return unsuccessful_workers
