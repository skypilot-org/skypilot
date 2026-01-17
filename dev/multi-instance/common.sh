# Common functions for multi-instance dev scripts.
# Source this file, don't execute it.

# Find repo root by looking for .git directory
find_repo_root() {
    local dir="$PWD"
    while [[ "$dir" != "/" ]]; do
        if [[ -d "$dir/.git" ]]; then
            echo "$dir"
            return 0
        fi
        dir="$(dirname "$dir")"
    done
    return 1
}

# Load instance configuration. Assumes .sky-dev/.instance exists.
load_instance() {
    local repo_root
    repo_root="$(find_repo_root)" || {
        echo "ERROR: Not in a git repository." >&2
        return 1
    }

    INSTANCE_DIR="$repo_root/.sky-dev"

    if [[ ! -f "$INSTANCE_DIR/.instance" ]]; then
        echo "ERROR: No .sky-dev/.instance found. Run setup.sh first." >&2
        return 1
    fi

    source "$INSTANCE_DIR/.instance"
    REPO_ROOT="$repo_root"
    CONTAINER_NAME="skypilot-dev-${INSTANCE_NAME}"
}

# Check Docker is available
check_docker() {
    if ! command -v docker &>/dev/null; then
        echo "ERROR: Docker is not installed or not in PATH." >&2
        return 1
    fi
    if ! docker info &>/dev/null; then
        echo "ERROR: Docker daemon is not running." >&2
        return 1
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
