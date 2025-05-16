#!/usr/bin/env python3
# Refer to https://docs.skypilot.co/en/latest/reservations/existing-machines.html for details on how to use this script.
import argparse
import os
import subprocess
import sys
import tempfile
import time
import yaml
from typing import Dict, List, Optional, Any, Tuple

# Colors for nicer UX
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
NC = '\033[0m'  # No color

DEFAULT_SSH_TARGETS_PATH = os.path.expanduser('~/.sky/ssh_node_pools.yaml')
DEFAULT_KUBECONFIG_PATH = os.path.expanduser('~/.kube/config')
SSH_CONFIG_PATH = os.path.expanduser('~/.ssh/config')

# Get the directory of this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def parse_args():
    parser = argparse.ArgumentParser(description='Deploy a Kubernetes cluster on remote machines.')
    parser.add_argument('--ips-file', dest='ips_file', help='File containing IP addresses or SSH host entries (one per line)')
    parser.add_argument('--user', help='Username to use for SSH (overridden by SSH config if host exists there)')
    parser.add_argument('--ssh-key', dest='ssh_key', help='Path to SSH private key (overridden by SSH config if host exists there)')
    parser.add_argument('--context-name', dest='context_name', default='default', help='Kubernetes context name')
    parser.add_argument('--cleanup', action='store_true', help='Clean up the cluster')
    parser.add_argument('--password', help='Password for sudo')
    parser.add_argument('--infra', help='Name of the cluster in ssh_node_pools.yaml to use')
    parser.add_argument('--ssh-targets-file', dest='ssh_targets_file', 
                        default=DEFAULT_SSH_TARGETS_PATH,
                        help=f'Path to SSH targets YAML file (default: {DEFAULT_SSH_TARGETS_PATH})')
    parser.add_argument('--kubeconfig-path', dest='kubeconfig_path',
                        default=DEFAULT_KUBECONFIG_PATH,
                        help=f'Path to save the kubeconfig file (default: {DEFAULT_KUBECONFIG_PATH})')
    parser.add_argument('--use-ssh-config', dest='use_ssh_config', action='store_true',
                        help='Use SSH config for host settings instead of explicit parameters')
    
    return parser.parse_args()

def load_ssh_targets(file_path: str) -> Dict[str, Any]:
    """Load SSH targets from YAML file."""
    if not os.path.exists(file_path):
        print(f"{RED}Error: SSH targets file not found: {file_path}{NC}", flush=True)
        sys.exit(1)
    
    try:
        with open(file_path, 'r') as f:
            targets = yaml.safe_load(f)
        return targets
    except Exception as e:
        print(f"{RED}Error loading SSH targets file: {e}{NC}", flush=True)
        sys.exit(1)

def check_host_in_ssh_config(hostname: str) -> bool:
    """Check if a hostname is defined in SSH config file."""
    if not os.path.exists(SSH_CONFIG_PATH):
        return False
    
    try:
        result = subprocess.run(["ssh", "-G", hostname], 
                               capture_output=True, text=True)
        # If successful, the host is in the SSH config
        return result.returncode == 0 and "hostname" in result.stdout
    except Exception:
        return False

def get_cluster_config(targets: Dict[str, Any], cluster_name: Optional[str] = None) -> Dict[str, Any]:
    """Get configuration for specific clusters or all clusters."""
    if not targets:
        print(f"{RED}Error: No clusters defined in SSH targets file{NC}", flush=True)
        sys.exit(1)
    
    if cluster_name:
        if cluster_name not in targets:
            print(f"{RED}Error: Cluster '{cluster_name}' not found in SSH targets file{NC}", flush=True)
            sys.exit(1)
        return {cluster_name: targets[cluster_name]}
    
    # Return all clusters if no specific cluster is specified
    return targets

def prepare_hosts_info(cluster_config: Dict[str, Any]) -> List[Dict[str, str]]:
    """Prepare list of hosts with resolved user, identity_file, and password."""
    if 'hosts' not in cluster_config or not cluster_config['hosts']:
        print(f"{RED}Error: No hosts defined in cluster configuration{NC}", flush=True)
        sys.exit(1)
    
    # Get cluster-level defaults
    cluster_user = cluster_config.get('user', '')
    cluster_identity_file = cluster_config.get('identity_file', '')
    cluster_password = cluster_config.get('password', '')
    
    hosts_info = []
    for host in cluster_config['hosts']:
        # Host can be a string (IP or SSH config hostname) or a dict
        if isinstance(host, str):
            # Check if this is an SSH config hostname
            is_ssh_config_host = check_host_in_ssh_config(host)
            
            hosts_info.append({
                'ip': host,
                'user': '' if is_ssh_config_host else cluster_user,
                'identity_file': '' if is_ssh_config_host else cluster_identity_file,
                'password': cluster_password,
                'use_ssh_config': is_ssh_config_host
            })
        else:
            # It's a dict with potential overrides
            if 'ip' not in host:
                print(f"{RED}Warning: Host missing 'ip' field, skipping: {host}{NC}", flush=True)
                continue
            
            # Check if this is an SSH config hostname
            is_ssh_config_host = check_host_in_ssh_config(host['ip'])
            
            # Use host-specific values or fall back to cluster defaults
            host_user = '' if is_ssh_config_host else host.get('user', cluster_user)
            host_identity_file = '' if is_ssh_config_host else host.get('identity_file', cluster_identity_file)
            host_password = host.get('password', cluster_password)
            
            hosts_info.append({
                'ip': host['ip'],
                'user': host_user,
                'identity_file': host_identity_file,
                'password': host_password,
                'use_ssh_config': is_ssh_config_host
            })
    
    return hosts_info

def run_command(cmd, shell=False):
    """Run a local command and return the output."""
    process = subprocess.run(cmd, shell=shell, capture_output=True, text=True)
    if process.returncode != 0:
        print(f"{RED}Error executing command: {cmd}{NC}", flush=True)
        print(f"STDOUT: {process.stdout}", flush=True)
        print(f"STDERR: {process.stderr}", flush=True)
        return None
    return process.stdout.strip()

def get_effective_host_ip(hostname: str) -> str:
    """Get the effective IP for a hostname from SSH config."""
    try:
        result = subprocess.run(
            ["ssh", "-G", hostname],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("hostname "):
                    return line.split(" ", 1)[1].strip()
    except Exception:
        pass
    return hostname  # Return the original hostname if lookup fails

def run_remote(node, cmd, user='', ssh_key='', connect_timeout=30, use_ssh_config=False):
    """Run a command on a remote machine via SSH."""
    if use_ssh_config:
        # Use SSH config for connection parameters
        ssh_cmd = ["ssh", node, cmd]
    else:
        # Use explicit parameters
        ssh_cmd = ["ssh", 
                  "-o", "StrictHostKeyChecking=no", 
                  "-o", "IdentitiesOnly=yes",
                  "-o", f"ConnectTimeout={connect_timeout}",
                  "-o", "ServerAliveInterval=10",
                  "-o", "ServerAliveCountMax=3"]
        
        if ssh_key:
            ssh_cmd.extend(["-i", ssh_key])
        
        ssh_cmd.append(f"{user}@{node}")
        ssh_cmd.append(cmd)
    
    process = subprocess.run(ssh_cmd, capture_output=True, text=True)
    if process.returncode != 0:
        print(f"{RED}Error executing command {cmd} on {node}:{NC}", flush=True)
        print(f"STDERR: {process.stderr}", flush=True)
        return None
    return process.stdout.strip()

def create_askpass_script(password):
    """Create an askpass script block for sudo with password."""
    if not password:
        return ""

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
    print(f"{YELLOW}➜ {message}{NC}", flush=True)

def success_message(message):
    """Show a success message."""
    print(f"{GREEN}✔ {message}{NC}", flush=True)

def cleanup_server_node(node, user, ssh_key, askpass_block, use_ssh_config=False):
    """Uninstall k3s and clean up the state on a server node."""
    print(f"{YELLOW}Cleaning up head node {node}...{NC}", flush=True)
    cmd = f"""
        {askpass_block}
        echo 'Uninstalling k3s...' &&
        sudo -A /usr/local/bin/k3s-uninstall.sh || true &&
        sudo -A rm -rf /etc/rancher /var/lib/rancher /var/lib/kubelet /etc/kubernetes ~/.kube
    """
    run_remote(node, cmd, user, ssh_key, use_ssh_config=use_ssh_config)
    print(f"{GREEN}Node {node} cleaned up successfully.{NC}", flush=True)

def cleanup_agent_node(node, user, ssh_key, askpass_block, use_ssh_config=False):
    """Uninstall k3s and clean up the state on an agent node."""
    print(f"{YELLOW}Cleaning up node {node}...{NC}", flush=True)
    cmd = f"""
        {askpass_block}
        echo 'Uninstalling k3s...' &&
        sudo -A /usr/local/bin/k3s-agent-uninstall.sh || true &&
        sudo -A rm -rf /etc/rancher /var/lib/rancher /var/lib/kubelet /etc/kubernetes ~/.kube
    """
    run_remote(node, cmd, user, ssh_key, use_ssh_config=use_ssh_config)
    print(f"{GREEN}Node {node} cleaned up successfully.{NC}", flush=True)

def check_gpu(node, user, ssh_key, use_ssh_config=False):
    """Check if a node has a GPU."""
    cmd = "command -v nvidia-smi &> /dev/null && nvidia-smi --query-gpu=gpu_name --format=csv,noheader &> /dev/null"
    result = run_remote(node, cmd, user, ssh_key, use_ssh_config=use_ssh_config)
    return result is not None

def ensure_directory_exists(path):
    """Ensure the directory for the specified file path exists."""
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

def setup_kubectl_ssh_tunnel(head_node, ssh_user, ssh_key, context_name, use_ssh_config=False):
    """Set up kubeconfig exec credential plugin for SSH tunnel"""
    progress_message("Setting up SSH tunnel for Kubernetes API access...")
    
    # Paths to scripts
    tunnel_script = os.path.join(SCRIPT_DIR, "ssh-tunnel.sh")
    cleanup_script = os.path.join(SCRIPT_DIR, "cleanup-tunnel.sh")
    
    # Make sure scripts are executable
    os.chmod(tunnel_script, 0o755)
    os.chmod(cleanup_script, 0o755)
    
    # Build the credential exec command
    if use_ssh_config:
        exec_cmd = f"{tunnel_script} --use-ssh-config --context {context_name} {head_node}"
    else:
        exec_cmd = f"{tunnel_script} --ssh-key {ssh_key} --context {context_name} {head_node} {ssh_user}"
    
    # Update kubeconfig to use localhost instead of remote IP
    run_command(["kubectl", "config", "set-cluster", context_name, 
                "--server=https://localhost:6443",
                "--insecure-skip-tls-verify=true"])
    
    # Set up exec credential plugin
    run_command([
        "kubectl", "config", "set-credentials", context_name,
        f"--exec-command={tunnel_script}",
        f"--exec-arg=--context",
        f"--exec-arg={context_name}",
        "--exec-api-version=client.authentication.k8s.io/v1beta1"
    ])
    
    if use_ssh_config:
        run_command([
            "kubectl", "config", "set-credentials", context_name,
            f"--exec-arg=--use-ssh-config",
            f"--exec-arg={head_node}"
        ])
    else:
        run_command([
            "kubectl", "config", "set-credentials", context_name,
            f"--exec-arg=--ssh-key",
            f"--exec-arg={ssh_key}",
            f"--exec-arg={head_node}",
            f"--exec-arg={ssh_user}"
        ])
    
    success_message("SSH tunnel configured through kubectl credential plugin")
    print(f"{GREEN}Your kubectl connection is now tunneled through SSH (port 6443).{NC}", flush=True)
    print(f"{GREEN}This tunnel will be automatically established when needed.{NC}", flush=True)

def cleanup_kubectl_ssh_tunnel(context_name):
    """Clean up the SSH tunnel for a specific context"""
    progress_message(f"Cleaning up SSH tunnel for context {context_name}...")
    
    # Path to cleanup script
    cleanup_script = os.path.join(SCRIPT_DIR, "cleanup-tunnel.sh")
    
    # Make sure script is executable
    if os.path.exists(cleanup_script):
        os.chmod(cleanup_script, 0o755)
        
        # Run the cleanup script
        subprocess.run([cleanup_script, context_name], 
                      stdout=subprocess.DEVNULL, 
                      stderr=subprocess.DEVNULL)
        
        success_message(f"SSH tunnel for context {context_name} cleaned up")
    else:
        print(f"{YELLOW}Cleanup script not found: {cleanup_script}{NC}", flush=True)

def main():
    args = parse_args()
    
    kubeconfig_path = os.path.expanduser(args.kubeconfig_path)
    global_use_ssh_config = args.use_ssh_config
    
    # Check if using YAML configuration or command line arguments
    if args.ips_file:
        # Using command line arguments - legacy mode
        if args.ssh_key and not os.path.isfile(args.ssh_key) and not global_use_ssh_config:
            print(f"{RED}Error: SSH key not found: {args.ssh_key}{NC}", flush=True)
            sys.exit(1)
        
        if not os.path.isfile(args.ips_file):
            print(f"{RED}Error: IPs file not found: {args.ips_file}{NC}", flush=True)
            sys.exit(1)
        
        with open(args.ips_file, 'r') as f:
            hosts = [line.strip() for line in f if line.strip()]
        
        if not hosts:
            print(f"{RED}Error: Hosts file is empty or not formatted correctly.{NC}", flush=True)
            sys.exit(1)
        
        head_node = hosts[0]
        worker_nodes = hosts[1:]
        ssh_user = args.user if not global_use_ssh_config else ''
        ssh_key = args.ssh_key if not global_use_ssh_config else ''
        context_name = args.context_name
        password = args.password
        
        # Check if hosts are in SSH config
        head_use_ssh_config = global_use_ssh_config or check_host_in_ssh_config(head_node)
        worker_use_ssh_config = [global_use_ssh_config or check_host_in_ssh_config(node) for node in worker_nodes]
        
        # Single cluster deployment for legacy mode
        deploy_cluster(head_node, worker_nodes, ssh_user, ssh_key, context_name, password, 
                       head_use_ssh_config, worker_use_ssh_config, kubeconfig_path, args.cleanup)
    else:
        # Using YAML configuration
        targets = load_ssh_targets(args.ssh_targets_file)
        clusters_config = get_cluster_config(targets, args.infra)
        
        # Process each cluster
        for cluster_name, cluster_config in clusters_config.items():
            print(f"{YELLOW}==== Deploying cluster: {cluster_name} ====${NC}", flush=True)
            hosts_info = prepare_hosts_info(cluster_config)
            
            if not hosts_info:
                print(f"{RED}Error: No valid hosts found for cluster '{cluster_name}'. Skipping.{NC}", flush=True)
                continue
            
            # Use the first host as the head node and the rest as worker nodes
            head_host = hosts_info[0]
            worker_hosts = hosts_info[1:] if len(hosts_info) > 1 else []
            
            head_node = head_host['ip']
            worker_nodes = [h['ip'] for h in worker_hosts]
            ssh_user = head_host['user']
            ssh_key = head_host['identity_file']
            head_use_ssh_config = global_use_ssh_config or head_host.get('use_ssh_config', False)
            worker_use_ssh_config = [global_use_ssh_config or h.get('use_ssh_config', False) for h in worker_hosts]
            password = head_host['password']
            
            # Generate a unique context name for each cluster
            context_name = args.context_name
            if context_name == 'default':
                context_name = 'ssh-' + cluster_name
            
            # Deploy this cluster
            deploy_cluster(head_node, worker_nodes, ssh_user, ssh_key, context_name, password,
                           head_use_ssh_config, worker_use_ssh_config, kubeconfig_path, args.cleanup,
                           worker_hosts=worker_hosts)
            
            print(f"{GREEN}==== Completed deployment for cluster: {cluster_name} ====${NC}", flush=True)

def deploy_cluster(head_node, worker_nodes, ssh_user, ssh_key, context_name, password,
                  head_use_ssh_config, worker_use_ssh_config, kubeconfig_path, cleanup, 
                  worker_hosts=None):
    """Deploy or clean up a single Kubernetes cluster."""
    # Ensure SSH key is expanded for paths with ~ (home directory)
    if ssh_key:
        ssh_key = os.path.expanduser(ssh_key)
    
    # Generate the askpass block if password is provided
    askpass_block = create_askpass_script(password)
    
    # Token for k3s
    k3s_token = "mytoken"  # Any string can be used as the token
    
    # Pre-flight checks
    print(f"{YELLOW}Checking SSH connection to head node...{NC}", flush=True)
    run_remote(head_node, "echo 'SSH connection successful'", ssh_user, ssh_key, use_ssh_config=head_use_ssh_config)
    
    # If --cleanup flag is set, uninstall k3s and exit
    if cleanup:
        print(f"{YELLOW}Starting cleanup...{NC}", flush=True)
        
        # Clean up SSH tunnel first
        cleanup_kubectl_ssh_tunnel(context_name)
        
        # Clean up head node
        cleanup_server_node(head_node, ssh_user, ssh_key, askpass_block, use_ssh_config=head_use_ssh_config)
        
        # Clean up worker nodes
        for i, node in enumerate(worker_nodes):
            # If using YAML config with specific worker info
            if worker_hosts and i < len(worker_hosts):
                worker_user = worker_hosts[i]['user']
                worker_key = worker_hosts[i]['identity_file']
                worker_password = worker_hosts[i]['password']
                worker_askpass = create_askpass_script(worker_password)
                worker_config = worker_use_ssh_config[i]
                cleanup_agent_node(node, worker_user, worker_key, worker_askpass, use_ssh_config=worker_config)
            else:
                cleanup_agent_node(node, ssh_user, ssh_key, askpass_block, use_ssh_config=worker_use_ssh_config[i])
        
        # Remove the context from local kubeconfig if it exists
        if os.path.isfile(kubeconfig_path):
            progress_message(f"Removing context '{context_name}' from local kubeconfig...")
            run_command(["kubectl", "config", "delete-context", context_name], shell=False)
            run_command(["kubectl", "config", "delete-cluster", context_name], shell=False)
            run_command(["kubectl", "config", "delete-user", context_name], shell=False)
            
            # Update the current context to the first available context
            contexts = run_command(["kubectl", "config", "view", "-o", "jsonpath='{.contexts[0].name}'"], shell=False)
            if contexts:
                run_command(["kubectl", "config", "use-context", contexts], shell=False)
            
            success_message(f"Context '{context_name}' removed from local kubeconfig.")
        
        print(f"{GREEN}Cleanup completed successfully.{NC}", flush=True)
        return
    
    # Get effective IP for master node if using SSH config - needed for workers to connect
    if head_use_ssh_config:
        effective_master_ip = get_effective_host_ip(head_node)
        print(f"{GREEN}Resolved head node {head_node} to {effective_master_ip} from SSH config{NC}", flush=True)
    else:
        effective_master_ip = head_node
    
    # Step 1: Install k3s on the head node
    progress_message(f"Deploying Kubernetes on head node ({head_node})...")
    cmd = f"""
        {askpass_block}
        curl -sfL https://get.k3s.io | K3S_TOKEN={k3s_token} sudo -E -A sh - &&
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
    run_remote(head_node, cmd, ssh_user, ssh_key, use_ssh_config=head_use_ssh_config)
    success_message("K3s deployed on head node.")
    
    # Check if head node has a GPU
    install_gpu = False
    if check_gpu(head_node, ssh_user, ssh_key, use_ssh_config=head_use_ssh_config):
        print(f"{YELLOW}GPU detected on head node ({head_node}).{NC}", flush=True)
        install_gpu = True
    
    # Fetch the head node's internal IP (this will be passed to worker nodes)
    master_addr = run_remote(head_node, "hostname -I | awk '{print $1}'", ssh_user, ssh_key, use_ssh_config=head_use_ssh_config)
    print(f"{GREEN}Master node internal IP: {master_addr}{NC}", flush=True)
    
    # Step 2: Install k3s on worker nodes and join them to the master node
    for i, node in enumerate(worker_nodes):
        progress_message(f"Deploying Kubernetes on worker node ({node})...")
        
        # If using YAML config with specific worker info
        if worker_hosts and i < len(worker_hosts):
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
        
        cmd = f"""
            {worker_askpass}
            curl -sfL https://get.k3s.io | K3S_URL=https://{master_addr}:6443 K3S_TOKEN={k3s_token} sudo -E -A sh -
        """
        run_remote(node, cmd, worker_user, worker_key, use_ssh_config=worker_config)
        success_message(f"Kubernetes deployed on worker node ({node}).")
        
        # Check if worker node has a GPU
        if check_gpu(node, worker_user, worker_key, use_ssh_config=worker_config):
            print(f"{YELLOW}GPU detected on worker node ({node}).{NC}", flush=True)
            install_gpu = True
    
    # Step 3: Configure local kubectl to connect to the cluster
    progress_message("Configuring local kubectl to connect to the cluster...")
    
    # Create temporary directory for kubeconfig operations
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_kubeconfig = os.path.join(temp_dir, "kubeconfig")
        
        # Get the kubeconfig from remote server
        if head_use_ssh_config:
            scp_cmd = ["scp", head_node + ":~/.kube/config", temp_kubeconfig]
        else:
            scp_cmd = [
                "scp", "-o", "StrictHostKeyChecking=no", "-o", "IdentitiesOnly=yes", 
                "-i", ssh_key, f"{ssh_user}@{head_node}:~/.kube/config", temp_kubeconfig
            ]
        run_command(scp_cmd, shell=False)
        
        # Create the directory for the kubeconfig file if it doesn't exist
        ensure_directory_exists(kubeconfig_path)
        
        # Create empty kubeconfig if it doesn't exist
        if not os.path.isfile(kubeconfig_path):
            open(kubeconfig_path, 'a').close()
        
        # Modify the temporary kubeconfig to update server address and context name
        modified_config = os.path.join(temp_dir, "modified_config")
        with open(temp_kubeconfig, 'r') as f_in:
            with open(modified_config, 'w') as f_out:
                in_cluster = False
                for line in f_in:
                    if "clusters:" in line:
                        in_cluster = True
                    elif "users:" in line:
                        in_cluster = False
                    
                    if in_cluster and "certificate-authority-data:" in line:
                        continue
                    elif in_cluster and "server:" in line:
                        # Initially just set to the effective master IP
                        # (will be changed to localhost by setup_kubectl_ssh_tunnel later)
                        f_out.write(f"    server: https://{effective_master_ip}:6443\n")
                        f_out.write(f"    insecure-skip-tls-verify: true\n")
                        continue
                    
                    # Replace default context names with user-provided context name
                    line = line.replace("name: default", f"name: {context_name}")
                    line = line.replace("cluster: default", f"cluster: {context_name}")
                    line = line.replace("user: default", f"user: {context_name}")
                    line = line.replace("current-context: default", f"current-context: {context_name}")
                    
                    f_out.write(line)
        
        # First check if context name exists and delete it if it does
        # TODO(romilb): Should we throw an error here instead? 
        run_command(["kubectl", "config", "delete-context", context_name], shell=False)
        run_command(["kubectl", "config", "delete-cluster", context_name], shell=False)
        run_command(["kubectl", "config", "delete-user", context_name], shell=False)
        
        # Merge the configurations using kubectl
        merged_config = os.path.join(temp_dir, "merged_config")
        os.environ["KUBECONFIG"] = f"{kubeconfig_path}:{modified_config}"
        with open(merged_config, 'w') as merged_file:
            kubectl_cmd = ["kubectl", "config", "view", "--flatten"]
            result = run_command(kubectl_cmd, shell=False)
            if result:
                merged_file.write(result)
        
        # Replace the kubeconfig with the merged config
        os.replace(merged_config, kubeconfig_path)
        
        # Set the new context as the current context
        run_command(["kubectl", "config", "use-context", context_name], shell=False)
    
    # Always set up SSH tunnel since we assume only port 22 is accessible
    setup_kubectl_ssh_tunnel(head_node, ssh_user, ssh_key, context_name, use_ssh_config=head_use_ssh_config)
    
    success_message(f"kubectl configured with new context '{context_name}'.")
    
    print(f"Cluster deployment completed. Kubeconfig saved to {kubeconfig_path}", flush=True)
    print("You can now run 'kubectl get nodes' to verify the setup.", flush=True)
    
    # Install GPU operator if a GPU was detected on any node
    if install_gpu:
        print(f"{YELLOW}GPU detected in the cluster. Installing Nvidia GPU Operator...{NC}", flush=True)
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
            while ! kubectl describe nodes --kubeconfig ~/.kube/config | grep -q 'nvidia.com/gpu:'; do
                echo 'Waiting for GPU operator...'
                sleep 5
            done
            echo 'GPU operator installed successfully.'
        """
        run_remote(head_node, cmd, ssh_user, ssh_key, use_ssh_config=head_use_ssh_config)
        success_message("GPU Operator installed.")
    else:
        print(f"{YELLOW}No GPUs detected. Skipping GPU Operator installation.{NC}", flush=True)
    
    # Configure SkyPilot
    progress_message("Configuring SkyPilot...")
    
    # The env var KUBECONFIG ensures sky check uses the right kubeconfig
    os.environ["KUBECONFIG"] = kubeconfig_path
    run_command(["sky", "check", "kubernetes"], shell=False)
    
    success_message("SkyPilot configured successfully.")
    
    # Display final success message
    print(f"{GREEN}==== 🎉 Kubernetes cluster deployment completed successfully 🎉 ====${NC}", flush=True)
    print("You can now interact with your Kubernetes cluster through SkyPilot: ", flush=True)
    print("  • List available GPUs: sky show-gpus --cloud kubernetes", flush=True)
    print("  • Launch a GPU development pod: sky launch -c devbox --cloud kubernetes --gpus A100:1", flush=True)
    print("  • Connect to pod with SSH: ssh devbox", flush=True)
    print("  • Connect to pod with VSCode: code --remote ssh-remote+devbox '/'", flush=True)

if __name__ == "__main__":
    main()