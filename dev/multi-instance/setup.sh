#!/bin/bash
# Setup script for creating an isolated SkyPilot development instance.
#
# This allows running multiple copies of SkyPilot simultaneously in different
# worktrees without conflicts. Each instance gets its own:
# - State directory (~/.sky/)
# - API server on a unique port
# - Logs and temporary files
#
# Usage:
#   cd /path/to/skypilot-worktree
#   ./dev/multi-instance/setup.sh [instance-name]
#
# After setup, add to your shell:
#   export PATH="$PWD/.sky-dev/bin:$PATH"

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
mkdir -p "$INSTANCE_DIR/home/.sky"
mkdir -p "$INSTANCE_DIR/home/sky_logs"
mkdir -p "$INSTANCE_DIR/home/.config"
mkdir -p "$INSTANCE_DIR/tmp"
mkdir -p "$INSTANCE_DIR/bin"

# Allocate a unique port
allocate_port() {
    for port in $(seq $PORT_RANGE_START $PORT_RANGE_END); do
        # Check if port is in use
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

# Create symlinks to real credentials (these need to be shared)
REAL_HOME="$HOME"
for cred_dir in .aws .azure .config/gcloud .kube .ssh; do
    src="$REAL_HOME/$cred_dir"
    dst="$INSTANCE_DIR/home/$cred_dir"
    if [[ -e "$src" ]] && [[ ! -e "$dst" ]]; then
        # Create parent directory if needed
        mkdir -p "$(dirname "$dst")"
        ln -sf "$src" "$dst"
        echo "Linked: $cred_dir"
    fi
done

# Create initial config.yaml with the correct API endpoint
cat > "$INSTANCE_DIR/home/.sky/config.yaml" << EOF
# Auto-generated config for isolated SkyPilot instance: $INSTANCE_NAME
# Port: $PORT
api_server:
  endpoint: http://127.0.0.1:$PORT
EOF
echo "Created config.yaml with endpoint http://127.0.0.1:$PORT"

# Copy the sky wrapper to bin/
cp "$SCRIPT_DIR/sky" "$INSTANCE_DIR/bin/sky"
chmod +x "$INSTANCE_DIR/bin/sky"

# Store instance metadata
cat > "$INSTANCE_DIR/.instance" << EOF
INSTANCE_NAME=$INSTANCE_NAME
INSTANCE_DIR=$INSTANCE_DIR
REPO_ROOT=$REPO_ROOT
PORT=$PORT
REAL_HOME=$REAL_HOME
CREATED=$(date -Iseconds)
EOF

echo ""
echo "===== Setup Complete ====="
echo ""
echo "To use this isolated instance, add to your shell:"
echo ""
echo "  export PATH=\"$INSTANCE_DIR/bin:\$PATH\""
echo ""
echo "Or for a single session:"
echo ""
echo "  source $INSTANCE_DIR/bin/activate"
echo ""
echo "Then use 'sky' commands as normal. The API server will run on port $PORT."
echo ""

# Also create an activate script for convenience
cat > "$INSTANCE_DIR/bin/activate" << EOF
# Source this file to activate the isolated SkyPilot instance
# Usage: source $INSTANCE_DIR/bin/activate

export PATH="$INSTANCE_DIR/bin:\$PATH"
export SKYPILOT_DEV_INSTANCE="$INSTANCE_NAME"

echo "Activated SkyPilot instance: $INSTANCE_NAME (port $PORT)"
echo "Run 'sky api start' to start the API server"
EOF

echo "Created activate script: $INSTANCE_DIR/bin/activate"
