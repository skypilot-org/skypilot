#!/usr/bin/env python3
# Refer to https://docs.skypilot.co/en/latest/reservations/existing-machines.html for details on how to use this script.
import argparse
import os
import subprocess
import sys
import tempfile
import time

# Colors for nicer UX
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
NC = '\033[0m'  # No color

def parse_args():
    parser = argparse.ArgumentParser(description='Deploy a Kubernetes cluster on remote machines.')
    parser.add_argument('ips_file', help='File containing IP addresses (one per line)')
    parser.add_argument('user', help='Username to use for SSH')
    parser.add_argument('ssh_key', help='Path to SSH private key')
    parser.add_argument('context_name', nargs='?', default='default', help='Kubernetes context name')
    parser.add_argument('--cleanup', action='store_true', help='Clean up the cluster')
    parser.add_argument('--password', help='Password for sudo')

    return parser.parse_args()

def run_command(cmd, shell=False):
    """Run a local command and return the output."""
    process = subprocess.run(cmd, shell=shell, capture_output=True, text=True)
    if process.returncode != 0:
        print(f"{RED}Error executing command: {cmd}{NC}")
        print(f"STDERR: {process.stderr}")
        return None
    return process.stdout.strip()

def run_remote(node_ip, cmd, user, ssh_key):
    """Run a command on a remote machine via SSH."""
    ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "IdentitiesOnly=yes", "-i", ssh_key, f"{user}@{node_ip}", cmd]
    process = subprocess.run(ssh_cmd, capture_output=True, text=True)
    if process.returncode != 0:
        print(f"{RED}Error executing command on {node_ip}:{NC}")
        print(f"STDERR: {process.stderr}")
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
    print(f"{YELLOW}➜ {message}{NC}")

def success_message(message):
    """Show a success message."""
    print(f"{GREEN}✔ {message}{NC}")

def cleanup_server_node(node_ip, user, ssh_key, askpass_block):
    """Uninstall k3s and clean up the state on a server node."""
    print(f"{YELLOW}Cleaning up head node {node_ip}...{NC}")
    cmd = f"""
        {askpass_block}
        echo 'Uninstalling k3s...' &&
        sudo -A /usr/local/bin/k3s-uninstall.sh || true &&
        sudo -A rm -rf /etc/rancher /var/lib/rancher /var/lib/kubelet /etc/kubernetes ~/.kube
    """
    run_remote(node_ip, cmd, user, ssh_key)
    print(f"{GREEN}Node {node_ip} cleaned up successfully.{NC}")

def cleanup_agent_node(node_ip, user, ssh_key, askpass_block):
    """Uninstall k3s and clean up the state on an agent node."""
    print(f"{YELLOW}Cleaning up node {node_ip}...{NC}")
    cmd = f"""
        {askpass_block}
        echo 'Uninstalling k3s...' &&
        sudo -A /usr/local/bin/k3s-agent-uninstall.sh || true &&
        sudo -A rm -rf /etc/rancher /var/lib/rancher /var/lib/kubelet /etc/kubernetes ~/.kube
    """
    run_remote(node_ip, cmd, user, ssh_key)
    print(f"{GREEN}Node {node_ip} cleaned up successfully.{NC}")

def check_gpu(node_ip, user, ssh_key):
    """Check if a node has a GPU."""
    cmd = "command -v nvidia-smi &> /dev/null && nvidia-smi --query-gpu=gpu_name --format=csv,noheader &> /dev/null"
    result = run_remote(node_ip, cmd, user, ssh_key)
    return result is not None

def main():
    args = parse_args()

    # Basic argument checks
    if not os.path.isfile(args.ssh_key):
        print(f"{RED}Error: SSH key not found: {args.ssh_key}{NC}")
        sys.exit(1)

    if not os.path.isfile(args.ips_file):
        print(f"{RED}Error: IPs file not found: {args.ips_file}{NC}")
        sys.exit(1)

    # Get head node and worker nodes from the IPs file
    with open(args.ips_file, 'r') as f:
        ips = [line.strip() for line in f if line.strip()]

    if not ips:
        print(f"{RED}Error: IPs file is empty or not formatted correctly.{NC}")
        sys.exit(1)

    head_node = ips[0]
    worker_nodes = ips[1:]

    # Generate the askpass block if password is provided
    askpass_block = create_askpass_script(args.password)

    # Token for k3s
    k3s_token = "mytoken"  # Any string can be used as the token

    # Pre-flight checks
    run_remote(head_node, "echo 'SSH connection successful'", args.user, args.ssh_key)

    # If --cleanup flag is set, uninstall k3s and exit
    if args.cleanup:
        print(f"{YELLOW}Starting cleanup...{NC}")

        # Clean up head node
        cleanup_server_node(head_node, args.user, args.ssh_key, askpass_block)

        # Clean up worker nodes
        for node in worker_nodes:
            cleanup_agent_node(node, args.user, args.ssh_key, askpass_block)

        # Remove the context from local kubeconfig if it exists
        kubeconfig_path = os.path.expanduser("~/.kube/config")
        if os.path.isfile(kubeconfig_path):
            progress_message(f"Removing context '{args.context_name}' from local kubeconfig...")
            run_command(["kubectl", "config", "delete-context", args.context_name], shell=False)
            run_command(["kubectl", "config", "delete-cluster", args.context_name], shell=False)
            run_command(["kubectl", "config", "delete-user", args.context_name], shell=False)

            # Update the current context to the first available context
            contexts = run_command(["kubectl", "config", "view", "-o", "jsonpath='{.contexts[0].name}'"], shell=False)
            if contexts:
                run_command(["kubectl", "config", "use-context", contexts], shell=False)

            success_message(f"Context '{args.context_name}' removed from local kubeconfig.")

        print(f"{GREEN}Cleanup completed successfully.{NC}")
        sys.exit(0)

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
    run_remote(head_node, cmd, args.user, args.ssh_key)
    success_message("K3s deployed on head node.")

    # Check if head node has a GPU
    install_gpu = False
    if check_gpu(head_node, args.user, args.ssh_key):
        print(f"{YELLOW}GPU detected on head node ({head_node}).{NC}")
        install_gpu = True

    # Fetch the head node's internal IP (this will be passed to worker nodes)
    master_addr = run_remote(head_node, "hostname -I | awk '{print $1}'", args.user, args.ssh_key)
    print(f"{GREEN}Master node internal IP: {master_addr}{NC}")

    # Step 2: Install k3s on worker nodes and join them to the master node
    for node in worker_nodes:
        progress_message(f"Deploying Kubernetes on worker node ({node})...")
        cmd = f"""
            {askpass_block}
            curl -sfL https://get.k3s.io | K3S_URL=https://{master_addr}:6443 K3S_TOKEN={k3s_token} sudo -E -A sh -
        """
        run_remote(node, cmd, args.user, args.ssh_key)
        success_message(f"Kubernetes deployed on worker node ({node}).")

        # Check if worker node has a GPU
        if check_gpu(node, args.user, args.ssh_key):
            print(f"{YELLOW}GPU detected on worker node ({node}).{NC}")
            install_gpu = True

    # Step 3: Configure local kubectl to connect to the cluster
    progress_message("Configuring local kubectl to connect to the cluster...")

    # Create temporary directory for kubeconfig operations
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_kubeconfig = os.path.join(temp_dir, "kubeconfig")

        # Get the kubeconfig from remote server
        scp_cmd = [
            "scp", "-o", "StrictHostKeyChecking=no", "-o", "IdentitiesOnly=yes",
            "-i", args.ssh_key, f"{args.user}@{head_node}:~/.kube/config", temp_kubeconfig
        ]
        subprocess.run(scp_cmd, check=True)

        # Create .kube directory if it doesn't exist
        kubeconfig_dir = os.path.expanduser("~/.kube")
        os.makedirs(kubeconfig_dir, exist_ok=True)

        # Create empty kubeconfig if it doesn't exist
        kubeconfig_file = os.path.join(kubeconfig_dir, "config")
        if not os.path.isfile(kubeconfig_file):
            open(kubeconfig_file, 'a').close()

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
                        f_out.write(f"    server: https://{head_node}:6443\n")
                        f_out.write(f"    insecure-skip-tls-verify: true\n")
                        continue

                    # Replace default context names with user-provided context name
                    line = line.replace("name: default", f"name: {args.context_name}")
                    line = line.replace("cluster: default", f"cluster: {args.context_name}")
                    line = line.replace("user: default", f"user: {args.context_name}")
                    line = line.replace("current-context: default", f"current-context: {args.context_name}")

                    f_out.write(line)

        # Merge the configurations using kubectl
        merged_config = os.path.join(temp_dir, "merged_config")
        os.environ["KUBECONFIG"] = f"{kubeconfig_file}:{modified_config}"
        subprocess.run(["kubectl", "config", "view", "--flatten"], stdout=open(merged_config, 'w'), check=True)

        # Replace the kubeconfig with the merged config
        os.replace(merged_config, kubeconfig_file)

        # Set the new context as the current context
        subprocess.run(["kubectl", "config", "use-context", args.context_name], check=True)

    success_message(f"kubectl configured with new context '{args.context_name}'.")

    print("Cluster deployment completed. You can now run 'kubectl get nodes' to verify the setup.")

    # Install GPU operator if a GPU was detected on any node
    if install_gpu:
        print(f"{YELLOW}GPU detected in the cluster. Installing Nvidia GPU Operator...{NC}")
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
        run_remote(head_node, cmd, args.user, args.ssh_key)
        success_message("GPU Operator installed.")
    else:
        print(f"{YELLOW}No GPUs detected. Skipping GPU Operator installation.{NC}")

    # Configure SkyPilot
    progress_message("Configuring SkyPilot...")
    subprocess.run(["sky", "check", "kubernetes"], check=True)
    success_message("SkyPilot configured successfully.")

    # Display final success message
    print(f"{GREEN}==== 🎉 Kubernetes cluster deployment completed successfully 🎉 ===={NC}")
    print("You can now interact with your Kubernetes cluster through SkyPilot: ")
    print("  • List available GPUs: sky show-gpus --cloud kubernetes")
    print("  • Launch a GPU development pod: sky launch -c devbox --cloud kubernetes --gpus A100:1")
    print("  • Connect to pod with SSH: ssh devbox")
    print("  • Connect to pod with VSCode: code --remote ssh-remote+devbox '/'")

if __name__ == "__main__":
    main()