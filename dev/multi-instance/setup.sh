#!/bin/bash
# Setup script for creating an isolated SkyPilot development instance.
# Creates .sky-dev/ directory with instance configuration.
#
# Usually called automatically by 'activate', but can be run directly.

set -e

# Find repo root
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

REPO_ROOT="$(find_repo_root)" || {
    echo "ERROR: Not in a git repository." >&2
    exit 1
}

INSTANCE_DIR="$REPO_ROOT/.sky-dev"
INSTANCE_NAME="${1:-$(basename "$REPO_ROOT")}"

# Skip if already set up
if [[ -f "$INSTANCE_DIR/.instance" ]]; then
    source "$INSTANCE_DIR/.instance"
    echo "Already set up: $INSTANCE_NAME (port $PORT)"
    exit 0
fi

echo "Setting up: $INSTANCE_NAME"

# Create directories
mkdir -p "$INSTANCE_DIR/state" "$INSTANCE_DIR/sky_logs"

# Derive port from repo path hash (deterministic, 46501-46599)
HASH=$(echo "$REPO_ROOT" | cksum | cut -d' ' -f1)
PORT=$((46501 + HASH % 99))

# Write config
cat > "$INSTANCE_DIR/.instance" << EOF
INSTANCE_NAME="$INSTANCE_NAME"
PORT="$PORT"
EOF

echo "Created .sky-dev/ (port $PORT)"
