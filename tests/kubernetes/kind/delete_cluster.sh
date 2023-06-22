# Deletes the local kind cluster
# Usage: ./delete_cluster.sh
set -e
kind delete cluster --name skypilot
echo "Kubernetes cluster deleted!"