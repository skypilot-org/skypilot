#!/bin/bash
echo -e "\033[1m===== SkyPilot Kubernetes cluster deployment script =====\033[0m"
echo -e "This script will deploy a Kubernetes cluster on the cloud and GPUs specified in cloud_k8s.yaml.\n"

set -ex

# Read cluster name from environment variable if it exists, else use default value
CLUSTER_NAME=${CLUSTER_NAME:-k8s}

# Deploy the k8s cluster
sky launch -y -c ${CLUSTER_NAME} cloud_k8s.yaml

# Get the endpoint of the k8s cluster
# Attempt to get the primary endpoint and handle any errors
PRIMARY_ENDPOINT=""
SKY_STATUS_OUTPUT=$(SKYPILOT_DEBUG=0 sky status --endpoint 6443 ${CLUSTER_NAME} 2>&1) || true

# Check if the command was successful and if the output contains a valid IP address
if [[ "$SKY_STATUS_OUTPUT" != *"ValueError"* ]]; then
  PRIMARY_ENDPOINT="$SKY_STATUS_OUTPUT"
else
  echo "Primary endpoint retrieval failed or unsupported. Falling back to alternate method..."
fi

# If primary endpoint is empty or invalid, try to fetch from SSH config
if [[ -z "$PRIMARY_ENDPOINT" ]]; then
  echo "Using alternate method to fetch endpoint..."

  # Parse the HostName from the SSH config file
  SSH_CONFIG_FILE="$HOME/.sky/generated/ssh/${CLUSTER_NAME}"
  if [[ -f "$SSH_CONFIG_FILE" ]]; then
    ENDPOINT=$(awk '/^ *HostName / { print $2; exit}' "$SSH_CONFIG_FILE")
    ENDPOINT="${ENDPOINT}:6443"
  fi

  if [[ -z "$ENDPOINT" ]]; then
    echo "Failed to retrieve a valid endpoint. Exiting."
    exit 1
  fi
else
  ENDPOINT="$PRIMARY_ENDPOINT"
  echo "Using primary endpoint: $ENDPOINT"
fi

# Rsync the remote kubeconfig to the local machine
mkdir -p ~/.kube
rsync -av ${CLUSTER_NAME}:'~/.kube/config' ~/.kube/config

KUBECONFIG_FILE="$HOME/.kube/config"

# Back up the original kubeconfig file if it exists
if [[ -f "$KUBECONFIG_FILE" ]]; then
  echo "Backing up kubeconfig file to ${KUBECONFIG_FILE}.bak"
  cp "$KUBECONFIG_FILE" "${KUBECONFIG_FILE}.bak"
fi

# Temporary file to hold the modified kubeconfig
TEMP_FILE=$(mktemp)

# Remove the certificate-authority-data, and replace the server with
awk '
  BEGIN { in_cluster = 0 }
  /^clusters:/ { in_cluster = 1 }
  /^users:/ { in_cluster = 0 }
  in_cluster && /^ *certificate-authority-data:/ { next }
  in_cluster && /^ *server:/ {
    print "    server: https://'${ENDPOINT}'"
    print "    insecure-skip-tls-verify: true"
    next
  }
  { print }
' "$KUBECONFIG_FILE" > "$TEMP_FILE"

# Replace the original kubeconfig with the modified one
mv "$TEMP_FILE" "$KUBECONFIG_FILE"

echo "Updated kubeconfig file successfully."

sleep 5 # Wait for the cluster to be ready
sky check kubernetes

set +x
echo -e "\033[1m===== Kubernetes cluster deployment complete =====\033[0m"
echo -e "You can now access your k8s cluster with kubectl and skypilot.\n"
echo -e "• View the list of available GPUs on Kubernetes: \033[1msky show-gpus --cloud kubernetes\033[0m"
echo -e "• To launch a SkyPilot job running nvidia-smi on this cluster: \033[1msky launch --cloud kubernetes --gpus <GPU> -- nvidia-smi\033[0m"

