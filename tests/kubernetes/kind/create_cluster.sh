# Be sure to have built the latest image before running this script
set -e
kind delete cluster
# If kind-cluster.yaml is not present, generate it
if [ ! -f kind-cluster.yaml ]; then
    echo "Generating kind-cluster.yaml"
    python portmap_gen.py
fi
kind create cluster --config kind-cluster.yaml
# Load local skypilot image on to the cluster for faster startup
kind load docker-image us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:latest
