#!/bin/bash
# Creates a local Kubernetes cluster using kind
# Usage: ./create_cluster.sh
# Invokes generate_kind_config.py to generate a kind-cluster.yaml with NodePort mappings
set -e

# Limit port range to speed up kind cluster creation
PORT_RANGE_START=30000
PORT_RANGE_END=30100

# Temporarily disable 'exit on error' to capture docker info output
set +e
docker_output=$(docker info 2>&1)
exit_status=$?
set -e

# Check if docker info command was successful
if [ $exit_status -ne 0 ]; then
    # Check for 'permission denied' in docker output
    if echo "$docker_output" | grep -q "permission denied"; then
        >&2 echo "Permission denied while trying to connect to the Docker daemon socket. Make sure your user is added to the docker group or has appropriate permissions. Instructions: https://docs.docker.com/engine/install/linux-postinstall/"
    else
        >&2 echo "Docker is not running. Please start Docker and try again."
    fi
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
    # Switch context to the local cluster
    kubectl config use-context kind-skypilot
    exit 100
fi

# Generate cluster YAML
echo "Generating /tmp/skypilot-kind.yaml"
python -m sky.utils.kubernetes.generate_kind_config --path /tmp/skypilot-kind.yaml --port-start ${PORT_RANGE_START} --port-end ${PORT_RANGE_END}

kind create cluster --config /tmp/skypilot-kind.yaml --name skypilot

# Load local skypilot image on to the cluster for faster startup
echo "Loading local skypilot image on to the cluster"
docker pull us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:latest
kind load docker-image --name skypilot us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:latest

# Print CPUs available on the local cluster
NUM_CPUS=$(kubectl get nodes -o jsonpath='{.items[0].status.capacity.cpu}')
echo "Kubernetes cluster ready! Run `sky check` to setup Kubernetes access."
echo "Number of CPUs available on the local cluster: $NUM_CPUS"
