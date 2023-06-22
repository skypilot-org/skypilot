# Creates a local Kubernetes cluster using kind
# Usage: ./create_cluster.sh
# Invokes portmap_gen.py to generate a kind-cluster.yaml with NodePort mappings
# Be sure to have built the latest image before running this script
set -e
kind delete cluster --name skypilot
# If kind-cluster.yaml is not present, generate it
if [ ! -f kind-cluster.yaml ]; then
    echo "Generating kind-cluster.yaml"
    python portmap_gen.py
fi
kind create cluster --config kind-cluster.yaml --name skypilot
# Load local skypilot image on to the cluster for faster startup
echo "Loading local skypilot image on to the cluster"
kind load docker-image --name skypilot us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:latest
# Print CPUs available on the local cluster
NUM_CPUS=$(kubectl get nodes -o jsonpath='{.items[0].status.capacity.cpu}')
echo "Kubernetes cluster ready! Run `sky check` to setup Kubernetes access."
echo "Number of CPUs available on the local cluster: $NUM_CPUS"