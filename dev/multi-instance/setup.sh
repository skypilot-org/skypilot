#!/bin/bash
# Setup script for creating an isolated SkyPilot development instance.
#
# This creates the instance directory structure and configuration.
# After setup, use start-container.sh to start the Docker container.
#
# Usage:
#   cd /path/to/skypilot-worktree
#   ./dev/multi-instance/setup.sh [instance-name]
#   source .sky-dev/bin/activate

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTANCE_DIR="$REPO_ROOT/.sky-dev"
INSTANCE_NAME="${1:-$(basename "$REPO_ROOT")}"

# Port range for isolated instances (46501-46599)
PORT_RANGE_START=46501
PORT_RANGE_END=46599

echo "Setting up isolated SkyPilot instance: $INSTANCE_NAME"
echo "Instance directory: $INSTANCE_DIR"

# Create directory structure
mkdir -p "$INSTANCE_DIR/state"       # Maps to /root/.sky in container
mkdir -p "$INSTANCE_DIR/sky_logs"    # Maps to /root/sky_logs in container
mkdir -p "$INSTANCE_DIR/bin"

# Allocate a unique port
allocate_port() {
    for port in $(seq $PORT_RANGE_START $PORT_RANGE_END); do
        # Check if port is in use on host
        if ! ss -tln 2>/dev/null | grep -q ":$port " && \
           ! netstat -tln 2>/dev/null | grep -q ":$port "; then
            # Also check if another instance already claimed this port
            claimed=false
            shopt -s nullglob
            for other_port_file in "$REPO_ROOT"/../*/.sky-dev/.port; do
                if [[ -f "$other_port_file" ]] && [[ "$(cat "$other_port_file")" == "$port" ]]; then
                    claimed=true
                    break
                fi
            done
            shopt -u nullglob
            if [[ "$claimed" == "false" ]]; then
                echo "$port"
                return 0
            fi
        fi
    done
    echo "ERROR: Could not allocate a port in range $PORT_RANGE_START-$PORT_RANGE_END" >&2
    return 1
}

# Allocate port if not already set
if [[ -f "$INSTANCE_DIR/.port" ]]; then
    PORT=$(cat "$INSTANCE_DIR/.port")
    echo "Using existing port: $PORT"
else
    PORT=$(allocate_port)
    echo "$PORT" > "$INSTANCE_DIR/.port"
    echo "Allocated port: $PORT"
fi

# Store instance metadata
REAL_HOME="$HOME"
cat > "$INSTANCE_DIR/.instance" << EOF
INSTANCE_NAME=$INSTANCE_NAME
INSTANCE_DIR=$INSTANCE_DIR
REPO_ROOT=$REPO_ROOT
PORT=$PORT
REAL_HOME=$REAL_HOME
CREATED=$(date -Iseconds)
EOF

# Copy scripts to bin/
cp "$SCRIPT_DIR/sky" "$INSTANCE_DIR/bin/sky"
cp "$SCRIPT_DIR/start-container.sh" "$INSTANCE_DIR/bin/start-container"
chmod +x "$INSTANCE_DIR/bin/sky" "$INSTANCE_DIR/bin/start-container"

# Create stop-container script
cat > "$INSTANCE_DIR/bin/stop-container" << 'EOF'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../.instance"
CONTAINER_NAME="skypilot-dev-${INSTANCE_NAME}"
echo "Stopping container: $CONTAINER_NAME"
docker stop "$CONTAINER_NAME" 2>/dev/null || echo "Container not running"
EOF
chmod +x "$INSTANCE_DIR/bin/stop-container"

# Create activate script
cat > "$INSTANCE_DIR/bin/activate" << EOF
# Source this file to activate the isolated SkyPilot instance
# Usage: source $INSTANCE_DIR/bin/activate

export PATH="$INSTANCE_DIR/bin:\$PATH"
export SKYPILOT_DEV_INSTANCE="$INSTANCE_NAME"
export SKYPILOT_DEV_PORT="$PORT"

echo "Activated SkyPilot instance: $INSTANCE_NAME"
echo "  Port: $PORT"
echo "  Container: skypilot-dev-$INSTANCE_NAME"
echo ""
echo "Commands:"
echo "  start-container  - Start the Docker container"
echo "  stop-container   - Stop the Docker container"
echo "  sky <cmd>        - Run sky commands in the container"
EOF

echo ""
echo "===== Setup Complete ====="
echo ""
echo "To activate this instance:"
echo ""
echo "  source $INSTANCE_DIR/bin/activate"
echo ""
echo "Then start the container and use sky:"
echo ""
echo "  start-container"
echo "  sky --help"
echo "  sky api start"
echo ""
