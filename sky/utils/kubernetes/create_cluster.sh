#!/bin/bash
# Creates a local Kubernetes cluster using kind with optional GPU support
# Usage: ./create_cluster.sh [name] [yaml_path] [--gpus]
set -e

# Images
IMAGE="us-docker.pkg.dev/sky-dev-465/skypilotk8s/skypilot:latest"
IMAGE_GPU="us-docker.pkg.dev/sky-dev-465/skypilotk8s/skypilot-gpu:latest"

# Arguments
NAME=$1
YAML_PATH=$2

# Check for GPU flag
ENABLE_GPUS=false
if [[ "$3" == "--gpus" ]]; then
    ENABLE_GPUS=true
fi

# ====== Dependency checks =======
# Initialize error message string
error_msg=""

# Temporarily disable 'exit on error' to capture docker info output
set +e
docker_output=$(docker info 2>&1)
exit_status=$?
set -e

# Check if docker info command was successful
if [ $exit_status -ne 0 ]; then
    if echo "$docker_output" | grep -q "permission denied"; then
        error_msg+="\n* Permission denied while trying to connect to the Docker daemon socket. Make sure your user is added to the docker group or has appropriate permissions.\nInstructions: https://docs.docker.com/engine/install/linux-postinstall/\n"
    else
        error_msg+="\n* Docker is not running. Please start Docker and try again.\n"
    fi
fi

# Check if kind is installed
if ! kind version > /dev/null 2>&1; then
    error_msg+="\n* kind is not installed. Please install kind and try again.\nInstallation instructions: https://kind.sigs.k8s.io/docs/user/quick-start/#installation\n"
fi

# Check if kubectl is installed
if ! kubectl > /dev/null 2>&1; then
    error_msg+="\n* kubectl is not installed. Please install kubectl and try again.\nInstallation instructions: https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/\n"
fi

if $ENABLE_GPUS; then
    # Check GPU dependencies. We don't automatically run sudo commands since the script may not have sudo permissions.
    # Check if nvidia-container-toolkit is already installed
    if ! dpkg -s nvidia-container-toolkit > /dev/null 2>&1; then
        error_msg+="\n* NVIDIA Container Toolkit not installed. Please install NVIDIA Container Toolkit and try again.\nInstallation instructions: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html#docker\n"
    fi

    # Check if NVIDIA is set as the default runtime for docker
    if ! grep -q '"default-runtime": "nvidia"' /etc/docker/daemon.json; then
        error_msg+="\n* NVIDIA is not set as the default runtime for Docker. To fix, run: \nsudo nvidia-ctk runtime configure --runtime=docker --set-as-default\nsudo systemctl restart docker\n"
    fi

    # Check if NVIDIA visible devices as configured as volume mounts
    if ! grep -q 'accept-nvidia-visible-devices-as-volume-mounts = true' /etc/nvidia-container-runtime/config.toml; then
        error_msg+="\n* NVIDIA visible devices are not set as volume mounts in container runtime. To fix, run: \nsudo sed -i '/accept-nvidia-visible-devices-as-volume-mounts/c\\\\accept-nvidia-visible-devices-as-volume-mounts = true' /etc/nvidia-container-runtime/config.toml\n"
    fi

    # Check if helm is installed
    if ! helm version > /dev/null 2>&1; then
        error_msg+="\n* helm is not installed. Please install helm and try again.\nInstallation instructions: https://helm.sh/docs/intro/install/\n"
    fi
fi

# Print the error message and exit if there are missing dependencies
if [ ! -z "$error_msg" ]; then
    >&2 echo "Some dependencies were not found or are not configured correctly. Please fix the following errors and try again:"
    error_msg=$(echo -e "$error_msg")
    >&2 printf "%s" "$error_msg" # Use printf to handle special characters
    exit 1
fi
# ====== End of dependency checks =======

# Check if the local cluster already exists
if kind get clusters | grep -q $NAME; then
    echo "Local cluster $NAME already exists. Exiting."
    # Switch context to the local cluster
    kind export kubeconfig --name $NAME
    kubectl config use-context kind-$NAME
    exit 100
fi

kind create cluster --config $YAML_PATH --name $NAME
echo "Kind cluster $NAME created."

# Function to wait for GPU operator to be correctly installed
wait_for_gpu_operator_installation() {
    echo "Starting wait for GPU operator installation..."

    SECONDS=0
    TIMEOUT=600  # 10 minutes in seconds

    while true; do
        if kubectl describe nodes | grep -q 'nvidia.com/gpu:'; then
            echo "GPU operator installed."
            break
        elif [ $SECONDS -ge $TIMEOUT ]; then
            echo "Timed out waiting for GPU operator installation."
            exit 1
        else
            echo "Waiting for GPU operator installation..."
            echo "To check status, see Nvidia GPU operator pods:"
            echo "kubectl get pods -n gpu-operator"
            sleep 5
        fi
    done
}

wait_for_nginx_ingress_controller_install() {
    echo "Starting installation of Nginx Ingress Controller..."

    SECONDS=0
    TIMEOUT=600  # 10 minutes in seconds

    kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml

    while true; do
        if kubectl get pod -n ingress-nginx -l app.kubernetes.io/component=controller -o wide | grep 'Running'; then
            echo "Nginx Ingress Controller installed."
            break
        elif [ $SECONDS -ge $TIMEOUT ]; then
            echo "Timed out waiting for installation of Nginx Ingress Controller."
            exit 1
        else
            echo "Waiting for Nginx Ingress Controller Installation..."
            echo "To check status, check Nginx Ingress Controller pods:"
            echo "kubectl get pod -n ingress-nginx -l app.kubernetes.io/component=controller -o wide"
            sleep 5
        fi
    done

}

if $ENABLE_GPUS; then
    echo "Enabling GPU support..."
    # Run patch for missing ldconfig.real
    # https://github.com/NVIDIA/nvidia-docker/issues/614#issuecomment-423991632
    docker exec -ti $NAME-control-plane /bin/bash -c '[ ! -f /sbin/ldconfig.real ] && ln -s /sbin/ldconfig /sbin/ldconfig.real || echo "/sbin/ldconfig.real already exists"'

    echo "Installing NVIDIA GPU operator..."
    # Install the NVIDIA GPU operator
    helm repo add nvidia https://helm.ngc.nvidia.com/nvidia || true
    helm repo update
    helm install --wait --generate-name \
         -n gpu-operator --create-namespace \
         nvidia/gpu-operator --set driver.enabled=false
    # Wait for GPU operator installation to succeed
    wait_for_gpu_operator_installation
fi

# Install the Nginx Ingress Controller
wait_for_nginx_ingress_controller_install

# Print CPUs available on the local cluster
NUM_CPUS=$(kubectl get nodes -o jsonpath='{.items[0].status.capacity.cpu}')
echo "Kubernetes cluster ready! Run `sky check` to setup Kubernetes access."
if $ENABLE_GPUS; then
    # As a sanity check, verify if GPU support is enabled
    if ! kubectl describe nodes | grep -q nvidia.com/gpu; then
        >&2 echo "GPU support was not enabled. Please check for any errors above."
        exit 1
    else
        echo "GPU support is enabled. Run 'sky show-gpus --cloud kubernetes' to see the GPUs available on the cluster."
    fi
fi
echo "Number of CPUs available on the local cluster $NAME: $NUM_CPUS"
