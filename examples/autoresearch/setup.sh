#!/usr/bin/env bash
set -euo pipefail

AUTORESEARCH_DIR="autoresearch"
EXAMPLES_BASE="https://raw.githubusercontent.com/skypilot-org/skypilot/master/examples/autoresearch"

echo "=== Autoresearch + SkyPilot setup ==="
echo ""

# 1. Install uv if missing
if ! command -v uv &>/dev/null; then
    echo "[1/4] Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # uv's installer drops the binary here; make it available for the rest of this script
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

# 3. Clone autoresearch
echo "[3/4] Cloning karpathy/autoresearch..."
if [ -d "$AUTORESEARCH_DIR" ]; then
    echo "      Directory '$AUTORESEARCH_DIR' already exists, skipping clone."
else
    git clone https://github.com/karpathy/autoresearch.git "$AUTORESEARCH_DIR"
fi

# 4. Download SkyPilot files into the cloned repo
#    (dependency installation and data prep are handled by experiment.yaml on the remote cluster)
echo "[4/4] Downloading experiment.yaml and instructions.md..."
curl -fsSL "$EXAMPLES_BASE/experiment.yaml" -o "$AUTORESEARCH_DIR/experiment.yaml"
curl -fsSL "$EXAMPLES_BASE/instructions.md"  -o "$AUTORESEARCH_DIR/instructions.md"

echo ""
echo "=== Credential check ==="
# Capture sky check output without letting its exit code kill the script,
# then inspect the text. (sky check exits non-zero when some clouds are absent,
# which would otherwise trip set -e / pipefail on the pipe.)
SKY_CHECK=$(sky check 2>&1 || true)
if echo "$SKY_CHECK" | grep -q ": enabled"; then
    echo "Cloud credentials OK."
else
    echo ""
    echo "No cloud credentials detected. Run 'sky check' to configure access."
    echo "Docs: https://docs.skypilot.co/en/latest/getting-started/installation.html#cloud-account-setup"
    echo ""
    echo "You can still continue - SkyPilot will prompt you when you launch the first job."
fi

echo ""
echo "================================================================"
echo " Setup complete!"
echo "================================================================"
echo ""
echo " Change into the working directory first:"
echo ""
echo "   cd $AUTORESEARCH_DIR"
echo ""
echo " Open Claude Code/Codex/any agent in this directory, then paste this prompt:"
echo ""
echo "   Read instructions.md and start running parallel experiments."
echo ""
echo " To target a specific cloud, tell your agent:"
echo "   '...set infra to aws'  (or gcp, azure, kubernetes, etc.)"
echo ""
echo " Monitor clusters: sky status"
echo " Stream logs:      sky logs <cluster-name>"
echo " Tear down all:    sky down -a -y"
echo "================================================================"
