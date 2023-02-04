kind delete cluster
kind create cluster --config cluster.yaml
# Load local skypilot image
kind load docker-image skypilot:latest
