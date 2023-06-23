# Creates a local Kubernetes cluster using kind
# Usage: ./create_cluster.sh
# Invokes generate_kind_config.py to generate a kind-cluster.yaml with NodePort mappings
# Be sure to have built the latest image before running this script
set -e

# Check if docker is running
if ! docker info > /dev/null 2>&1; then
    >&2 echo "Docker is not running. Please start Docker and try again."
    exit 1
fi

# Check if kind is installed
if ! kind version > /dev/null 2>&1; then
    >&2 echo "kind is not installed. Please install kind and try again. Installation instructions: https://kind.sigs.k8s.io/docs/user/quick-start/#installation."
    exit 1
fi

# Check if the local cluster already exists
if kind get clusters | grep -q skypilot; then
    echo "Local cluster already exists. Exiting."
    exit 100
fi

# If /tmp/skypilot-kind.yaml is not present, generate it
if [ ! -f /tmp/skypilot-kind.yaml ]; then
    echo "Generating /tmp/skypilot-kind.yaml"
    python -m sky.utils.kubernetes.generate_kind_config --path /tmp/skypilot-kind.yaml
fi

kind create cluster --config /tmp/skypilot-kind.yaml --name skypilot

# Load local skypilot image on to the cluster for faster startup
echo "Loading local skypilot image on to the cluster"
kind load docker-image --name skypilot us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:latest

# Print CPUs available on the local cluster
NUM_CPUS=$(kubectl get nodes -o jsonpath='{.items[0].status.capacity.cpu}')
echo "Kubernetes cluster ready! Run `sky check` to setup Kubernetes access."
echo "Number of CPUs available on the local cluster: $NUM_CPUS"