#!/bin/bash
# Stops a user Ray cluster on a SkyPilot cluster.
#
# This script stops a Ray cluster running on custom ports (default 6379),
# which is separate from SkyPilot's internal Ray cluster (port 6380).
#
# IMPORTANT: This script uses pkill to stop Ray processes, NOT 'ray stop',
# as 'ray stop' can interfere with SkyPilot's internal operations.
#
# Environment Variables:
#   RAY_HEAD_PORT=6379                     - Ray head node port to stop
#   RAY_CMD=ray                            - (Optional) Command to invoke Ray (e.g., "uv run ray")
#
# Usage:
#   # Stop default Ray cluster (port 6379)
#   ~/sky_templates/ray/stop_ray_cluster.sh
#
#   # Stop Ray cluster on custom port
#   export RAY_HEAD_PORT=6385
#   ~/sky_templates/ray/stop_ray_cluster.sh
#
#   # With uv
#   export RAY_CMD="uv run ray"
#   ~/sky_templates/ray/stop_ray_cluster.sh

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

RAY_HEAD_PORT=${RAY_HEAD_PORT:-6379}
RAY_CMD=${RAY_CMD:-ray}
# Tokenize the command string into an array so multi-word commands (e.g., "uv run ray")
# are handled safely when expanded later.
eval "RAY_CMD_ARR=( ${RAY_CMD} )"

run_ray() {
    "${RAY_CMD_ARR[@]}" "$@"
}

echo -e "${GREEN}Stopping Ray cluster on port ${RAY_HEAD_PORT}...${NC}"

RAY_ADDRESS="127.0.0.1:${RAY_HEAD_PORT}"
if [ "$SKYPILOT_NODE_RANK" -ne 0 ]; then
    HEAD_IP=$(echo "$SKYPILOT_NODE_IPS" | head -n1)
    RAY_ADDRESS="${HEAD_IP}:${RAY_HEAD_PORT}"
fi

# Check if Ray is running
if ! run_ray status --address="${RAY_ADDRESS}" &> /dev/null; then
    echo -e "${YELLOW}No Ray cluster found running on port ${RAY_HEAD_PORT}.${NC}"
    exit 0
fi

# Use pkill to stop Ray processes instead of 'ray stop'
# This prevents interfering with SkyPilot's internal Ray cluster (port 6380)
echo -e "${YELLOW}Killing Ray processes on port ${RAY_HEAD_PORT}...${NC}"

pkill -f "ray.*[=:]${RAY_HEAD_PORT}" || true

echo -e "${GREEN}Ray processes killed.${NC}"
# Wait a moment for processes to terminate
sleep 5

# Verify Ray is stopped
if run_ray status --address="${RAY_ADDRESS}" &> /dev/null; then
    echo -e "${RED}Warning: Ray cluster may still be running. Try manually:${NC}"
    echo -e "${RED}  pkill -9 -f 'ray.*[=:]${RAY_HEAD_PORT}'${NC}"
    exit 1
else
    echo -e "${GREEN}Ray cluster successfully stopped.${NC}"
fi
