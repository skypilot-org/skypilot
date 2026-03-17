#!/bin/bash
# Test SkyPilot skill triggering accuracy.
#
# Tests whether the skill correctly triggers (or doesn't trigger) for a set
# of eval queries. Uses --plugin-dir to load the skill as a proper plugin,
# matching how it works in interactive Claude Code sessions.
#
# Prerequisites:
#   - claude CLI installed and authenticated
#
# Usage (from repo root):
#   bash skills/test/test.sh
#   bash skills/test/test.sh --timeout 60 --workers 3

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if ! command -v claude &>/dev/null; then
    echo "Error: claude CLI not found. Install it first:"
    echo "  npm install -g @anthropic-ai/claude-code"
    exit 1
fi

echo "SkyPilot Skill Trigger Test"
echo "==========================="
echo ""

cd "$REPO_ROOT"
python3 "$SCRIPT_DIR/test_trigger.py" "$@"
