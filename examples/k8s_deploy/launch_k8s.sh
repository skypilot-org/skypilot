#!/bin/bash
set -ex

# Read cluster name from environment variable if it exists, else use default value
CLUSTER_NAME=${CLUSTER_NAME:-k8s}

sky launch -y -c ${CLUSTER_NAME} deploy_k8s.yaml

# Get the endpoint of the k8s cluster
ENDPOINT=$(SKYPILOT_DEBUG=0 sky status --endpoint 6443 ${CLUSTER_NAME})

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
