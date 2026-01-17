#!/bin/bash
# Start (or create) the isolated SkyPilot development container.
#
# This script:
# 1. Creates a Docker container if it doesn't exist
# 2. Starts the container if it's stopped
# 3. Sets up the Python venv on first run
#
# The container persists and can be accessed from multiple terminals.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTANCE_DIR="$REPO_ROOT/.sky-dev"

# Load instance config
if [[ ! -f "$INSTANCE_DIR/.instance" ]]; then
    echo "ERROR: Instance not set up. Run setup.sh first." >&2
    exit 1
fi
source "$INSTANCE_DIR/.instance"

CONTAINER_NAME="skypilot-dev-${INSTANCE_NAME}"
IMAGE="ubuntu:22.04"

# Check if container exists
container_exists() {
    docker container inspect "$CONTAINER_NAME" &>/dev/null
}

# Check if container is running
container_running() {
    [[ "$(docker container inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null)" == "true" ]]
}

# Create the container if it doesn't exist
create_container() {
    echo "Creating container: $CONTAINER_NAME"

    # Prepare mount options for credentials
    CRED_MOUNTS=""
    for cred_dir in .aws .azure .config/gcloud .kube .ssh; do
        if [[ -e "$REAL_HOME/$cred_dir" ]]; then
            CRED_MOUNTS="$CRED_MOUNTS -v $REAL_HOME/$cred_dir:/root/$cred_dir:ro"
        fi
    done

    docker create \
        --name "$CONTAINER_NAME" \
        --hostname "skypilot-dev" \
        -it \
        -p "${PORT}:46580" \
        -v "$REPO_ROOT:/app" \
        -v "$INSTANCE_DIR/state:/root/.sky" \
        -v "$INSTANCE_DIR/sky_logs:/root/sky_logs" \
        -w /app \
        $CRED_MOUNTS \
        -e "SKYPILOT_DEV=1" \
        "$IMAGE" \
        sleep infinity

    echo "Container created."
}

# Start the container
start_container() {
    if ! container_running; then
        echo "Starting container: $CONTAINER_NAME"
        docker start "$CONTAINER_NAME"
    else
        echo "Container already running: $CONTAINER_NAME"
    fi
}

# Set up the venv inside the container (idempotent)
setup_venv() {
    echo "Ensuring venv is set up..."

    docker exec "$CONTAINER_NAME" bash -c '
        set -e

        # Install system dependencies if needed
        if ! command -v python3 &>/dev/null || ! command -v pip3 &>/dev/null; then
            echo "Installing Python and dependencies..."
            apt-get update -qq
            apt-get install -y -qq python3 python3-pip python3-venv git curl > /dev/null
        fi

        # Create venv if it does not exist
        if [[ ! -d /app/.sky-dev/venv ]]; then
            echo "Creating venv..."
            python3 -m venv /app/.sky-dev/venv
        fi

        # Activate and install skypilot in dev mode
        source /app/.sky-dev/venv/bin/activate

        # Check if skypilot is installed
        if ! pip show skypilot &>/dev/null; then
            echo "Installing skypilot in dev mode..."
            pip install -q -e "/app"
        fi

        echo "Venv ready."
    '
}

# Main
if ! container_exists; then
    create_container
fi

start_container
setup_venv

echo ""
echo "Container ready: $CONTAINER_NAME"
echo "Port mapping: localhost:$PORT -> container:46580"
echo ""
echo "Use the sky wrapper or run directly:"
echo "  docker exec -it $CONTAINER_NAME /app/.sky-dev/venv/bin/sky <command>"
