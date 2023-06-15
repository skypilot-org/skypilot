# Be sure to have built the latest image before running this script
# If running on apple silicon:
# docker buildx build --load --platform linux/arm64 -t us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:latest -f Dockerfile_k8s ./sky && docker tag us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:latest skypilot:latest && docker push us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:latest
set -e
kind delete cluster
kind create cluster --config kind-cluster.yaml
# Load local skypilot image
kind load docker-image us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:latest
