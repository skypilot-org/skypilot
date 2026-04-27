#!/usr/bin/env bash
set -euo pipefail

# Autonomous Code Optimization with SkyPilot -- one-command setup.
#
# Usage:
#   TARGET_REPO=https://github.com/valkey-io/valkey.git bash setup.sh
#   TARGET_REPO=https://github.com/postgres/postgres.git bash setup.sh
#
# Or without TARGET_REPO to set up in an existing project directory:
#   cd /path/to/your/project && bash setup.sh

EXAMPLES_BASE="https://raw.githubusercontent.com/skypilot-org/skypilot/master/examples/autonomous-code-optimization"

echo "=== Autonomous Code Optimization + SkyPilot setup ==="
echo ""

# 1. Install uv if missing
if ! command -v uv &>/dev/null; then
    echo "[1/4] Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="${UV_TOOL_BIN_DIR:-$HOME/.local/bin}:$HOME/.cargo/bin:$PATH"
else
    echo "[1/4] uv already installed ($(uv --version))"
fi

# 2. Install SkyPilot if missing
if ! command -v sky &>/dev/null; then
    echo "[2/4] Installing SkyPilot via uv..."
    uv tool install skypilot
    export PATH="${UV_TOOL_BIN_DIR:-$HOME/.local/bin}:$HOME/.cargo/bin:$PATH"
else
    echo "[2/4] SkyPilot already installed ($(sky --version 2>/dev/null || echo 'unknown version'))"
fi

# 3. Clone target repo (if TARGET_REPO is set)
if [ -n "${TARGET_REPO:-}" ]; then
    PROJECT_DIR=$(basename "$TARGET_REPO" .git)
    echo "[3/4] Cloning $TARGET_REPO..."
    if [ -d "$PROJECT_DIR" ]; then
        echo "      Directory '$PROJECT_DIR' already exists, skipping clone."
    else
        git clone --depth 1 "$TARGET_REPO" "$PROJECT_DIR"
    fi
    cd "$PROJECT_DIR"
else
    PROJECT_DIR="$(basename "$(pwd)")"
    echo "[3/4] No TARGET_REPO set, using current directory: $(pwd)"
    if [ ! -d .git ]; then
        echo "      WARNING: Not a git repository. Run 'git init' first."
    fi
fi

# 4. Download SkyPilot autoresearch files
echo "[4/4] Downloading experiment.yaml and instructions.md..."
curl -fsSL "$EXAMPLES_BASE/experiment.yaml" -o experiment.yaml
curl -fsSL "$EXAMPLES_BASE/instructions.md"  -o instructions.md

echo ""
echo "=== Credential check ==="
SKY_CHECK=$(sky check 2>&1 || true)
if echo "$SKY_CHECK" | grep -q ": enabled"; then
    echo "Cloud credentials OK."
else
    echo ""
    echo "No cloud credentials detected. Run 'sky check' to configure access."
    echo "Docs: https://docs.skypilot.co/en/latest/getting-started/installation.html#cloud-account-setup"
    echo ""
    echo "You can still continue -- SkyPilot will prompt you when you launch the first job."
fi

echo ""
echo "================================================================"
echo " Setup complete!  Project: $PROJECT_DIR"
echo "================================================================"
echo ""
echo " Open Claude Code / Codex / any coding agent in this directory,"
echo " then paste this prompt:"
echo ""
echo "   Follow instructions.md to identify significant optimizations in"
echo "   $PROJECT_DIR. Feel free to use latest research literature to find"
echo "   creative and new techniques. Use 4 SkyPilot VMs simultaneously."
echo ""
echo " Example prompts:"
echo "   'Follow instructions.md to optimize llama.cpp for CPU inference tokens/sec."
echo "    Use latest research literature. Use 4 SkyPilot VMs simultaneously. Use AWS infra.'"
echo "   'Follow instructions.md to optimize valkey for SET ops/sec."
echo "    Use latest research literature. Use 4 SkyPilot VMs simultaneously. Use GCP infra.'"
echo "   'Follow instructions.md to optimize PostgreSQL for pgbench TPS."
echo "    Use latest research literature. Use 4 SkyPilot VMs simultaneously. Use AWS infra.'"
echo ""
echo " Monitor VMs:   sky status"
echo " Stream logs:   sky logs <vm-name>"
echo " Tear down all: sky down -a -y"
echo "================================================================"
