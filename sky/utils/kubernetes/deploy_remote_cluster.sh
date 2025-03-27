#!/bin/bash
# Refer to https://docs.skypilot.co/en/latest/reservations/existing-machines.html for details on how to use this script.
set -e

# Colors for nicer UX
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No color

# Variables
CLEANUP=false
INSTALL_GPU=false
POSITIONAL_ARGS=()
PASSWORD=""

# Process all arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --cleanup)
            CLEANUP=true
            shift
            ;;
        --password)
            PASSWORD=$2
            shift
            shift
            ;;
        *)
            POSITIONAL_ARGS+=("$1")
            shift
            ;;
    esac
done

# Restore positional arguments in correct order
set -- "${POSITIONAL_ARGS[@]}"

# Assign positional arguments to variables
IPS_FILE=$1
USER=$2
SSH_KEY=$3
CONTEXT_NAME=${4:-default}
K3S_TOKEN=mytoken  # Any string can be used as the token
# Create temporary askpass script for sudo
ASKPASS_BLOCK="# Create temporary askpass script
ASKPASS_SCRIPT=\$(mktemp)
trap 'rm -f \$ASKPASS_SCRIPT' EXIT INT TERM ERR QUIT
cat > \$ASKPASS_SCRIPT << EOF
#!/bin/bash
echo $PASSWORD
EOF
chmod 700 \$ASKPASS_SCRIPT
# Use askpass
export SUDO_ASKPASS=\$ASKPASS_SCRIPT
"

# Basic argument checks
if [ -z "$IPS_FILE" ] || [ -z "$USER" ] || [ -z "$SSH_KEY" ]; then
    >&2 echo -e "${RED}Error: Missing required arguments.${NC}"
    >&2 echo "Usage: ./deploy_remote_cluster.sh ips.txt username path/to/ssh/key [context-name] [--cleanup] [--password password]"
    exit 1
fi

# Check if SSH key exists
if [ ! -f "$SSH_KEY" ]; then
    >&2 echo -e "${RED}Error: SSH key not found: $SSH_KEY${NC}"
    exit 1
fi

# Check if IPs file exists
if [ ! -f "$IPS_FILE" ]; then
    >&2 echo -e "${RED}Error: IPs file not found: $IPS_FILE${NC}"
    exit 1
fi

# Get head node and worker nodes from the IPs file
HEAD_NODE=$(head -n 1 "$IPS_FILE")
WORKER_NODES=$(tail -n +2 "$IPS_FILE")

# Check if the IPs file is empty or not formatted correctly
if [ -z "$HEAD_NODE" ]; then
    >&2 echo -e "${RED}Error: IPs file is empty or not formatted correctly.${NC}"
    exit 1
fi

# Function to show a progress message
progress_message() {
    echo -e "${YELLOW}➜ $1${NC}"
}

# Step to display success
success_message() {
    echo -e "${GREEN}✔ $1${NC}"
}

# Function to run a command on a remote machine via SSH
run_remote() {
    local NODE_IP=$1
    local CMD=$2
    # echo -e "${YELLOW}Running command on $NODE_IP...${NC}"
    ssh -o StrictHostKeyChecking=no -o IdentitiesOnly=yes -i "$SSH_KEY" "$USER@$NODE_IP" "$CMD"
}

# Function to uninstall k3s and clean up the state on a remote machine
cleanup_server_node() {
    local NODE_IP=$1
    echo -e "${YELLOW}Cleaning up head node $NODE_IP...${NC}"
    run_remote "$NODE_IP" "
        $ASKPASS_BLOCK
        echo 'Uninstalling k3s...' &&
        sudo -A /usr/local/bin/k3s-uninstall.sh || true &&
        sudo -A rm -rf /etc/rancher /var/lib/rancher /var/lib/kubelet /etc/kubernetes ~/.kube
    "
    echo -e "${GREEN}Node $NODE_IP cleaned up successfully.${NC}"
}

# Function to uninstall k3s and clean up the state on a remote machine
cleanup_agent_node() {
    local NODE_IP=$1
    echo -e "${YELLOW}Cleaning up node $NODE_IP...${NC}"
    run_remote "$NODE_IP" "
        $ASKPASS_BLOCK
        echo 'Uninstalling k3s...' &&
        sudo -A /usr/local/bin/k3s-agent-uninstall.sh || true &&
        sudo -A rm -rf /etc/rancher /var/lib/rancher /var/lib/kubelet /etc/kubernetes ~/.kube
    "
    echo -e "${GREEN}Node $NODE_IP cleaned up successfully.${NC}"
}

check_gpu() {
    local NODE_IP=$1
    if run_remote "$NODE_IP" "command -v nvidia-smi &> /dev/null && nvidia-smi --query-gpu=gpu_name --format=csv,noheader &> /dev/null"; then
        return 0  # GPU detected
    else
        return 1  # No GPU detected
    fi
}

# Pre-flight checks
run_remote "$HEAD_NODE" "echo 'SSH connection successful'"
# TODO: Add more pre-flight checks here, including checking if port 6443 is accessible

# If --cleanup flag is set, uninstall k3s and exit
if [ "$CLEANUP" == "true" ]; then
    echo -e "${YELLOW}Starting cleanup...${NC}"

    # Clean up head node
    cleanup_server_node "$HEAD_NODE"

    # Clean up worker nodes
    for NODE in $WORKER_NODES; do
        cleanup_agent_node "$NODE"
    done

    # Remove the context from local kubeconfig if it exists
    if [ -f "$HOME/.kube/config" ]; then
        progress_message "Removing context '$CONTEXT_NAME' from local kubeconfig..."
        kubectl config delete-context "$CONTEXT_NAME" 2>/dev/null || true
        kubectl config delete-cluster "$CONTEXT_NAME" 2>/dev/null || true
        kubectl config delete-user "$CONTEXT_NAME" 2>/dev/null || true
        # Update the current context to the first available context
        kubectl config use-context $(kubectl config view -o jsonpath='{.contexts[0].name}') 2>/dev/null || true
        success_message "Context '$CONTEXT_NAME' removed from local kubeconfig."
    fi

    echo -e "${GREEN}Cleanup completed successfully.${NC}"
    exit 0
fi

# Step 1: Install k3s on the head node
progress_message "Deploying Kubernetes on head node ($HEAD_NODE)..."
run_remote "$HEAD_NODE" "
    $ASKPASS_BLOCK
    curl -sfL https://get.k3s.io | K3S_TOKEN=$K3S_TOKEN sudo -E -A sh - &&
    mkdir -p ~/.kube &&
    sudo -A cp /etc/rancher/k3s/k3s.yaml ~/.kube/config &&
    sudo -A chown \$(id -u):\$(id -g) ~/.kube/config &&
    for i in {1..3}; do
        if kubectl wait --for=condition=ready node --all --timeout=2m --kubeconfig ~/.kube/config; then
            break
        else
            echo 'Waiting for nodes to be ready...'
            sleep 5
        fi
    done
    if [ \$i -eq 3 ]; then
        echo 'Failed to wait for nodes to be ready after 3 attempts'
        exit 1
    fi"
success_message "K3s deployed on head node."

# Check if head node has a GPU
if check_gpu "$HEAD_NODE"; then
    echo -e "${YELLOW}GPU detected on head node ($HEAD_NODE).${NC}"
    INSTALL_GPU=true
fi

# Fetch the head node's internal IP (this will be passed to worker nodes)
MASTER_ADDR=$(run_remote "$HEAD_NODE" "hostname -I | awk '{print \$1}'")

echo -e "${GREEN}Master node internal IP: $MASTER_ADDR${NC}"

# Step 2: Install k3s on worker nodes and join them to the master node
for NODE in $WORKER_NODES; do
    progress_message "Deploying Kubernetes on worker node ($NODE)..."
    run_remote "$NODE" "
        $ASKPASS_BLOCK
        curl -sfL https://get.k3s.io | K3S_URL=https://$MASTER_ADDR:6443 K3S_TOKEN=$K3S_TOKEN sudo -E -A sh -"
    success_message "Kubernetes deployed on worker node ($NODE)."

    # Check if worker node has a GPU
    if check_gpu "$NODE"; then
        echo -e "${YELLOW}GPU detected on worker node ($NODE).${NC}"
        INSTALL_GPU=true
    fi
done
# Step 3: Configure local kubectl to connect to the cluster
progress_message "Configuring local kubectl to connect to the cluster..."

# Create temporary directory for kubeconfig operations
TEMP_DIR=$(mktemp -d)
TEMP_KUBECONFIG="$TEMP_DIR/kubeconfig"

# Get the kubeconfig from remote server
scp -o StrictHostKeyChecking=no -o IdentitiesOnly=yes -i "$SSH_KEY" "$USER@$HEAD_NODE":~/.kube/config "$TEMP_KUBECONFIG"

# Create .kube directory if it doesn't exist
mkdir -p "$HOME/.kube"

# Create empty kubeconfig if it doesn't exist
KUBECONFIG_FILE="$HOME/.kube/config"
if [[ ! -f "$KUBECONFIG_FILE" ]]; then
    touch "$KUBECONFIG_FILE"
fi

# Modify the temporary kubeconfig to update server address and context name
awk -v context="$CONTEXT_NAME" '
  /^clusters:/ { in_cluster = 1 }
  /^users:/ { in_cluster = 0 }
  in_cluster && /^ *certificate-authority-data:/ { next }
  in_cluster && /^ *server:/ {
    print "    server: https://'${HEAD_NODE}:6443'"
    print "    insecure-skip-tls-verify: true"
    next
  }
  /name: default/ { sub("name: default", "name: " context) }
  /cluster: default/ { sub("cluster: default", "cluster: " context) }
  /user: default/ { sub("user: default", "user: " context) }
  /current-context: default/ { sub("current-context: default", "current-context: " context) }
  { print }
' "$TEMP_KUBECONFIG" > "$TEMP_DIR/modified_config"

# Merge the configurations using kubectl
KUBECONFIG="$KUBECONFIG_FILE:$TEMP_DIR/modified_config" kubectl config view --flatten > "$TEMP_DIR/merged_config"
mv "$TEMP_DIR/merged_config" "$KUBECONFIG_FILE"

# Set the new context as the current context
kubectl config use-context "$CONTEXT_NAME"

# Clean up temporary files
rm -rf "$TEMP_DIR"

success_message "kubectl configured with new context '$CONTEXT_NAME'."

echo "Cluster deployment completed. You can now run 'kubectl get nodes' to verify the setup."

# Install GPU operator if a GPU was detected on any node
if [ "$INSTALL_GPU" == "true" ]; then
    echo -e "${YELLOW}GPU detected in the cluster. Installing Nvidia GPU Operator...${NC}"
    run_remote "$HEAD_NODE" "
        $ASKPASS_BLOCK
        curl -fsSL -o get_helm.sh https://raw.githubusercontent.com/helm/helm/master/scripts/get-helm-3 &&
        chmod 700 get_helm.sh &&
        ./get_helm.sh &&
        helm repo add nvidia https://helm.ngc.nvidia.com/nvidia && helm repo update &&
        kubectl create namespace gpu-operator --kubeconfig ~/.kube/config || true &&
        sudo -A ln -s /sbin/ldconfig /sbin/ldconfig.real || true &&
        helm install gpu-operator -n gpu-operator --create-namespace nvidia/gpu-operator \
        --set 'toolkit.env[0].name=CONTAINERD_CONFIG' \
        --set 'toolkit.env[0].value=/var/lib/rancher/k3s/agent/etc/containerd/config.toml' \
        --set 'toolkit.env[1].name=CONTAINERD_SOCKET' \
        --set 'toolkit.env[1].value=/run/k3s/containerd/containerd.sock' \
        --set 'toolkit.env[2].name=CONTAINERD_RUNTIME_CLASS' \
        --set 'toolkit.env[2].value=nvidia' &&
        echo 'Waiting for GPU operator installation...' &&
        while ! kubectl describe nodes --kubeconfig ~/.kube/config | grep -q 'nvidia.com/gpu:'; do
            echo 'Waiting for GPU operator...'
            sleep 5
        done
        echo 'GPU operator installed successfully.'"
    success_message "GPU Operator installed."
else
    echo -e "${YELLOW}No GPUs detected. Skipping GPU Operator installation.${NC}"
fi

# Configure SkyPilot
progress_message "Configuring SkyPilot..."
sky check kubernetes
success_message "SkyPilot configured successfully."

# Display final success message
echo -e "${GREEN}==== 🎉 Kubernetes cluster deployment completed successfully 🎉 ====${NC}"
echo "You can now interact with your Kubernetes cluster through SkyPilot: "
echo "  • List available GPUs: sky show-gpus --cloud kubernetes"
echo "  • Launch a GPU development pod: sky launch -c devbox --cloud kubernetes --gpus A100:1"
echo "  • Connect to pod with SSH: ssh devbox"
echo "  • Connect to pod with VSCode: code --remote ssh-remote+devbox '/'"
