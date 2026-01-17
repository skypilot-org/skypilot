#!/bin/bash
# Setup script for creating an isolated SkyPilot development instance.
#
# Creates .sky-dev/ directory with instance configuration.
# After setup, use sky/start-container/stop-container from dev/multi-instance/
#
# Usage:
#   cd /path/to/skypilot-worktree
#   ./dev/multi-instance/setup.sh [instance-name]

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
mkdir -p "$INSTANCE_DIR/state"       # Container's /root/.sky
mkdir -p "$INSTANCE_DIR/sky_logs"    # Container's /root/sky_logs

# Allocate a unique port
allocate_port() {
    for port in $(seq $PORT_RANGE_START $PORT_RANGE_END); do
        if ! ss -tln 2>/dev/null | grep -q ":$port " && \
           ! netstat -tln 2>/dev/null | grep -q ":$port "; then
            # Check sibling worktrees
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

if [[ -f "$INSTANCE_DIR/.port" ]]; then
    PORT=$(cat "$INSTANCE_DIR/.port")
    echo "Using existing port: $PORT"
else
    PORT=$(allocate_port)
    echo "$PORT" > "$INSTANCE_DIR/.port"
    echo "Allocated port: $PORT"
fi

# Write instance config
cat > "$INSTANCE_DIR/.instance" << EOF
INSTANCE_NAME="$INSTANCE_NAME"
REPO_ROOT="$REPO_ROOT"
PORT="$PORT"
EOF

echo ""
echo "===== Setup Complete ====="
echo ""
echo "Add dev/multi-instance to your PATH (once, in .bashrc or similar):"
echo ""
echo "  export PATH=\"$SCRIPT_DIR:\$PATH\""
echo ""
echo "Then from this directory, use:"
echo ""
echo "  start-container   # Start the Docker container"
echo "  sky --help        # Run sky commands"
echo "  stop-container    # Stop the container"
echo ""
