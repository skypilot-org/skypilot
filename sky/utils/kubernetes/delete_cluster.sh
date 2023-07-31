#!/bin/bash
# Deletes the local kind cluster
# Usage: ./delete_cluster.sh
# Raises error code 100 if the local cluster does not exist

set -e
# Check if docker is running
if ! docker info > /dev/null 2>&1; then
    >&2 echo "Docker is not running. Please start Docker and try again."
    exit 1
fi

# Check if kind is installed
if ! kind version > /dev/null 2>&1; then
    >&2 echo "kind is not installed. Please install kind and try again."
    exit 1
fi

# Check if the local cluster exists
if ! kind get clusters | grep -q skypilot; then
    echo "Local cluster does not exist. Exiting."
    exit 100
fi

kind delete cluster --name skypilot
echo "Local cluster deleted!"

# Switch to the first available context
AVAILABLE_CONTEXT=$(kubectl config get-contexts -o name | head -n 1)
if [ ! -z "$AVAILABLE_CONTEXT" ]; then
    echo "Switching to context $AVAILABLE_CONTEXT"
    kubectl config use-context $AVAILABLE_CONTEXT
fi
