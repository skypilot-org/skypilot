#!/bin/bash
# Starts a Ray cluster on a SkyPilot cluster.
#
# This script starts a Ray cluster using default Ray ports (6379, 8265),
# which are different from SkyPilot's system Ray ports (6380, 8266).
# This allows users to run their own Ray applications independently of
# SkyPilot's internal Ray cluster.
#
# Environment Variables:
#   RAY_HEAD_PORT=6379                     - Ray head node port
#   RAY_DASHBOARD_PORT=8265                - Ray dashboard port
#   RAY_DASHBOARD_HOST=127.0.0.1           - Dashboard host (set to 0.0.0.0 to expose externally)
#   RAY_DASHBOARD_AGENT_LISTEN_PORT=       - (Optional) Dashboard agent listen port
#   RAY_HEAD_IP_ADDRESS=                   - (Optional) Node IP address
#   RAY_CMD_PREFIX=                        - (Optional) Command prefix (e.g., "uv run")
#
# Usage:
#   ~/sky_templates/ray/start_cluster.sh
#
#   # With custom configurations
#   export RAY_DASHBOARD_HOST=0.0.0.0
#   export RAY_DASHBOARD_PORT=8280
#   ~/sky_templates/ray/start_cluster.sh
#
#   # With uv
#   export RAY_CMD_PREFIX="uv run"
#   ~/sky_templates/ray/start_cluster.sh

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

RAY_HEAD_PORT=${RAY_HEAD_PORT:-6379}
RAY_DASHBOARD_PORT=${RAY_DASHBOARD_PORT:-8265}
RAY_DASHBOARD_HOST=${RAY_DASHBOARD_HOST:-127.0.0.1}
RAY_DASHBOARD_AGENT_LISTEN_PORT=${RAY_DASHBOARD_AGENT_LISTEN_PORT:-}
RAY_HEAD_IP_ADDRESS=${RAY_HEAD_IP_ADDRESS:-}
RAY_CMD_PREFIX=${RAY_CMD_PREFIX:-}  # Optional command prefix (e.g., "uv run")

echo -e "${GREEN}Starting Ray cluster...${NC}"

# Ensure ray[default] is installed (we need [default] to do `ray list nodes`)
# Pin to existing version if Ray is already installed to avoid upgrading existing version.
RAY_VERSION=$(${RAY_CMD_PREFIX} ray --version 2>/dev/null | cut -d' ' -f3 || echo "")
if [ -n "${RAY_VERSION}" ]; then
    # Pin to existing version.
    VERSION_SPEC="==${RAY_VERSION}"
else
    echo -e "${YELLOW}Installing ray[default]...${NC}"
    VERSION_SPEC=""
fi

uv pip install "ray[default]${VERSION_SPEC}" || uv pip install --system "ray[default]${VERSION_SPEC}"

# Verify Ray is working
if ! ${RAY_CMD_PREFIX} ray --version &> /dev/null 2>&1; then
    echo -e "${RED}Error: Failed to install Ray.${NC}"
    exit 1
fi
echo -e "${GREEN}Ray $(${RAY_CMD_PREFIX} ray --version | cut -d' ' -f3) is installed.${NC}"

RAY_ADDRESS="127.0.0.1:${RAY_HEAD_PORT}"
if [ "${SKYPILOT_NODE_RANK}" -ne 0 ]; then
    HEAD_IP=$(echo "${SKYPILOT_NODE_IPS}" | head -n1)
    RAY_ADDRESS="${HEAD_IP}:${RAY_HEAD_PORT}"
fi

# Check if user-space Ray is already running
if ${RAY_CMD_PREFIX} ray status --address="${RAY_ADDRESS}" &> /dev/null; then
    echo -e "${YELLOW}Ray cluster is already running.${NC}"
    ${RAY_CMD_PREFIX} ray status --address="${RAY_ADDRESS}"
    exit 0
fi

TIMEOUT=300

if [ "${SKYPILOT_NODE_RANK}" -eq 0 ]; then
    echo -e "${GREEN}Starting Ray head node...${NC}"

    RAY_START_CMD="ray start --head \
        --port=${RAY_HEAD_PORT} \
        --dashboard-port=${RAY_DASHBOARD_PORT} \
        --dashboard-host=${RAY_DASHBOARD_HOST} \
        --disable-usage-stats \
        --include-dashboard=True"

    # Add --num-gpus only if > 0
    if [ "${SKYPILOT_NUM_GPUS_PER_NODE}" -gt 0 ]; then
        RAY_START_CMD="${RAY_START_CMD} --num-gpus=${SKYPILOT_NUM_GPUS_PER_NODE}"
    fi

    # Add optional dashboard agent listen port if specified
    if [ -n "${RAY_DASHBOARD_AGENT_LISTEN_PORT}" ]; then
        RAY_START_CMD="${RAY_START_CMD} --dashboard-agent-listen-port=${RAY_DASHBOARD_AGENT_LISTEN_PORT}"
    fi

    # Add optional node IP address if specified
    if [ -n "${RAY_HEAD_IP_ADDRESS}" ]; then
        RAY_START_CMD="${RAY_START_CMD} --node-ip-address=${RAY_HEAD_IP_ADDRESS}"
    fi

    eval "${RAY_CMD_PREFIX} ${RAY_START_CMD}"

    start_time=$(date +%s)
    while ! ${RAY_CMD_PREFIX} ray health-check --address="${RAY_ADDRESS}" &>/dev/null; do
        if [ "$(( $(date +%s) - start_time ))" -ge "$TIMEOUT" ]; then
            echo -e "${RED}Timed out waiting for head node. Exiting.${NC}" >&2
            exit 1
        fi
        echo "Head node not healthy yet. Retrying in 1s..."
        sleep 1
    done

    echo -e "${GREEN}Head node started successfully.${NC}"

    # Wait for all worker nodes to join
    if [ "${SKYPILOT_NUM_NODES}" -gt 1 ]; then
        echo "Waiting for all ${SKYPILOT_NUM_NODES} nodes to join..."
        start_time=$(date +%s)
        while true; do
            if [ "$(( $(date +%s) - start_time ))" -ge "${TIMEOUT}" ]; then
                echo -e "${RED}Error: Timeout waiting for nodes.${NC}" >&2
                exit 1
            fi
            ready_nodes=$(${RAY_CMD_PREFIX} ray list nodes --format=json | python3 -c "import sys, json; print(len(json.load(sys.stdin)))")
            if [ "${ready_nodes}" -ge "${SKYPILOT_NUM_NODES}" ]; then
                break
            fi
            echo "Waiting... (${ready_nodes} / ${SKYPILOT_NUM_NODES} nodes ready)"
            sleep 5
        done
        echo -e "${GREEN}All ${SKYPILOT_NUM_NODES} nodes have joined.${NC}"
    fi

    # Add sleep to after `ray start` to give ray enough time to daemonize
    sleep 5
else
    echo -e "${GREEN}Starting Ray worker node...${NC}"

    echo "Waiting for head node at ${RAY_ADDRESS}..."
    start_time=$(date +%s)
    while ! ${RAY_CMD_PREFIX} ray health-check --address="${RAY_ADDRESS}" &>/dev/null; do
        if [ "$(( $(date +%s) - start_time ))" -ge "$TIMEOUT" ]; then
            echo -e "${RED}Timed out waiting for head node. Exiting.${NC}" >&2
            exit 1
        fi
        echo "Head node not healthy yet. Retrying in 1s..."
        sleep 1
    done

    echo -e "${GREEN}Head node is healthy. Starting worker node...${NC}"
    WORKER_CMD="ray start --address=${RAY_ADDRESS} --disable-usage-stats"

    # Add --num-gpus only if > 0
    if [ "${SKYPILOT_NUM_GPUS_PER_NODE}" -gt 0 ]; then
        WORKER_CMD="${WORKER_CMD} --num-gpus=${SKYPILOT_NUM_GPUS_PER_NODE}"
    fi

    eval "${RAY_CMD_PREFIX} ${WORKER_CMD}"

    echo -e "${GREEN}Worker node started successfully.${NC}"

    # Add sleep to after `ray start` to give ray enough time to daemonize
    sleep 5
fi
