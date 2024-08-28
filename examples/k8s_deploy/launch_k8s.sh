#!/bin/bash
set -ex

# Read cluster name from environment variable if it exists, else use default value
CLUSTER_NAME=${CLUSTER_NAME:-k8s}

#sky launch -y -c ${CLUSTER_NAME} deploy_k8s.yaml

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
echo "You can now access your k8s cluster using kubectl."
