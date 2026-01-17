# Common functions for multi-instance dev scripts.
# Source this file, don't execute it.

# Find .sky-dev/.instance by walking up from current directory
find_instance() {
    local dir="$PWD"
    while [[ "$dir" != "/" ]]; do
        if [[ -f "$dir/.sky-dev/.instance" ]]; then
            echo "$dir/.sky-dev"
            return 0
        fi
        dir="$(dirname "$dir")"
    done
    return 1
}

# Load instance configuration
load_instance() {
    INSTANCE_DIR="$(find_instance)" || {
        echo "ERROR: No .sky-dev/.instance found in current directory or parents." >&2
        echo "Run setup.sh first: ./dev/multi-instance/setup.sh" >&2
        exit 1
    }
    source "$INSTANCE_DIR/.instance"
    CONTAINER_NAME="skypilot-dev-${INSTANCE_NAME}"
}

# Check Docker is available
check_docker() {
    if ! command -v docker &>/dev/null; then
        echo "ERROR: Docker is not installed or not in PATH." >&2
        exit 1
    fi
    if ! docker info &>/dev/null; then
        echo "ERROR: Docker daemon is not running." >&2
        exit 1
    fi
}

# Check if container exists
container_exists() {
    docker container inspect "$CONTAINER_NAME" &>/dev/null
}

# Check if container is running
container_running() {
    [[ "$(docker container inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null)" == "true" ]]
}
