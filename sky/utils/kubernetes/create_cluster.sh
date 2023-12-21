#!/bin/bash
# Creates a local Kubernetes cluster using kind with optional GPU support
# Usage: ./create_cluster.sh [--gpus]
# Invokes generate_kind_config.py to generate a kind-cluster.yaml with NodePort mappings
set -e

# Limit port range to speed up kind cluster creation
PORT_RANGE_START=30000
PORT_RANGE_END=30100

# Check for GPU flag
ENABLE_GPUS=false
if [[ "$1" == "--gpus" ]]; then
    ENABLE_GPUS=true
fi

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
    # Switch context to the local cluster
    kubectl config use-context kind-skypilot
    exit 100
fi

# Generate cluster YAML
echo "Generating /tmp/skypilot-kind.yaml"
python -m sky.utils.kubernetes.generate_kind_config --path /tmp/skypilot-kind.yaml --port-start ${PORT_RANGE_START} --port-end ${PORT_RANGE_END}

if $ENABLE_GPUS; then
    # Add GPU support. We don't run sudo commands since the script may not have sudo permissions.

    # Check if nvidia-container-toolkit is already installed
    if ! dpkg -s nvidia-container-toolkit > /dev/null 2>&1; then
        >&2 echo "NVIDIA Container Toolkit not installed. Please install NVIDIA Container Toolkit and try again. Installation instructions: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html#docker"
        exit 1
    else
        echo "NVIDIA Container Toolkit found."
    fi

    # Check if NVIDIA is set as the default runtime for docker
    if ! grep -q '"default-runtime": "nvidia"' /etc/docker/daemon.json; then
        >&2 echo "NVIDIA is not set as the default runtime for Docker. To fix, run:"
        >&2 echo "sudo nvidia-ctk runtime configure --runtime=docker --set-as-default"
        >&2 echo "sudo systemctl restart docker"
        exit 1
    else
        echo "NVIDIA is the default runtime for Docker"
    fi

    # Check if NVIDIA visible devices as configured as volume mounts
    if ! grep -q 'accept-nvidia-visible-devices-as-volume-mounts = true' /etc/nvidia-container-runtime/config.toml; then
        >&2 echo "NVIDIA visible devices are not set as volume mounts in container runtime. To fix, run:"
        >&2 echo "sudo sed -i '/accept-nvidia-visible-devices-as-volume-mounts/c\accept-nvidia-visible-devices-as-volume-mounts = true' /etc/nvidia-container-runtime/config.toml"
        exit 1
    else
        echo "NVIDIA visible devices are set as volume mounts"
    fi
fi

kind create cluster --config /tmp/skypilot-kind.yaml --name skypilot

# Function to wait for SkyPilot GPU labeling jobs to complete
wait_for_gpu_labeling_jobs() {
    echo "Waiting for SkyPilot GPU labeling jobs to complete..."

    while true; do
        TOTAL_JOBS=$(kubectl get jobs -n kube-system -l job=sky-gpu-labeler --no-headers | wc -l)
        COMPLETED_JOBS=$(kubectl get jobs -n kube-system -l job=sky-gpu-labeler --no-headers | grep "1/1" | wc -l)

        if [[ $COMPLETED_JOBS -eq $TOTAL_JOBS ]]; then
            echo "All SkyPilot GPU labeling jobs ($TOTAL_JOBS) completed."
            break
        else
            echo "Waiting for GPU labeling jobs to complete... ($COMPLETED_JOBS/$TOTAL_JOBS completed)"
            sleep 5
        fi
    done
}

# Function to wait for GPU operator to be correctly installed
wait_for_gpu_operator_installation() {
    echo "Waiting for GPU operator to be installed correctly..."

    while true; do
        if kubectl describe nodes | grep -q 'nvidia.com/gpu'; then
            echo "GPU operator installed correctly."
            break
        else
            echo "Waiting for GPU operator installation..."
            sleep 5
        fi
    done
}

if $ENABLE_GPUS; then
    # Run patch for missing ldconfig.real
    # https://github.com/NVIDIA/nvidia-docker/issues/614#issuecomment-423991632
    docker exec -ti skypilot-control-plane ln -s /sbin/ldconfig /sbin/ldconfig.real

    # Check if helm is installed
    if ! helm version > /dev/null 2>&1; then
        >&2 echo "helm is not installed. Please install helm and try again. Installation instructions: https://helm.sh/docs/intro/install/"
        exit 1
    fi
    # Install the NVIDIA GPU operator
    helm repo add nvidia https://helm.ngc.nvidia.com/nvidia || true
    helm repo update
    helm install --wait --generate-name \
         -n gpu-operator --create-namespace \
         nvidia/gpu-operator --set driver.enabled=false

    # Wait for GPU operator installation to succeed
    wait_for_gpu_operator_installation

    # Label nodes with GPUs
    python -m sky.utils.kubernetes.gpu_labeler

    # Wait for all the GPU labeling jobs to complete
    wait_for_gpu_labeling_jobs
fi

# Load local skypilot image on to the cluster for faster startup
echo "Loading local skypilot image on to the cluster"
docker pull us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:latest
kind load docker-image --name skypilot us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:latest

# Print CPUs available on the local cluster
NUM_CPUS=$(kubectl get nodes -o jsonpath='{.items[0].status.capacity.cpu}')
echo "Kubernetes cluster ready! Run `sky check` to setup Kubernetes access."
if $ENABLE_GPUS; then
    # Check if GPU support is enabled
    if ! kubectl describe nodes | grep -q nvidia.com/gpu; then
        >&2 echo "GPU support is not enabled. Please enable GPU support and try again."
        exit 1
    else
        echo "GPU support is enabled. Run 'sky show-gpus --cloud kubernetes' to see the GPUs available on the cluster."
    fi
fi
echo "Number of CPUs available on the local cluster: $NUM_CPUS"
