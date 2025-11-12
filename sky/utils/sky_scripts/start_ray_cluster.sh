#!/bin/bash
# Starts a Ray cluster on a SkyPilot cluster.
#
# This script starts a Ray cluster using default Ray ports (6379, 8265),
# which are different from SkyPilot's system Ray ports (6380, 8266).
# This allows users to run their own Ray applications independently of
# SkyPilot's internal Ray cluster.
#
# Usage:
#   ~/skypilot_scripts/start_ray_cluster.sh

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}Starting Ray cluster...${NC}"

# Check if Ray is installed
if ! command -v ray &> /dev/null; then
    echo -e "${YELLOW}Ray is not installed. Installing...${NC}"
    uv pip install --system ray
    if ! command -v ray &> /dev/null; then
        echo -e "${RED}Error: Failed to install Ray.${NC}"
        exit 1
    fi
    echo -e "${GREEN}Ray $(ray --version | cut -d' ' -f3) installed successfully.${NC}"
fi

RAY_ADDRESS="127.0.0.1:6379"
if [ "$SKYPILOT_NODE_RANK" -ne 0 ]; then
    HEAD_IP=$(echo "$SKYPILOT_NODE_IPS" | head -n1)
    RAY_ADDRESS="${HEAD_IP}:6379"
fi

# Check if user-space Ray is already running (port 6379)
if ray status --address="${RAY_ADDRESS}" &> /dev/null; then
    echo -e "${YELLOW}Ray cluster is already running.${NC}"
    ray status --address="${RAY_ADDRESS}"
    exit 0
fi

TIMEOUT=300

if [ "$SKYPILOT_NODE_RANK" -eq 0 ]; then
    echo -e "${GREEN}Starting Ray head node...${NC}"

    ray start --head \
        --port=6379 \
        --disable-usage-stats


    start_time=$(date +%s)
    while ! ray health-check --address="${RAY_ADDRESS}" &>/dev/null; do
        if [ "$(( $(date +%s) - start_time ))" -ge "$TIMEOUT" ]; then
            echo -e "${RED}Timed out waiting for head node. Exiting.${NC}" >&2
            exit 1
        fi
        echo "Head node not healthy yet. Retrying in 1s..."
        sleep 1
    done

    echo -e "${GREEN}Head node started successfully.${NC}"

    # Wait for all worker nodes to join
    if [ "$SKYPILOT_NUM_NODES" -gt 1 ]; then
        echo "Waiting for all $SKYPILOT_NUM_NODES nodes to join..."
        start_time=$(date +%s)
        while true; do
            if [ "$(( $(date +%s) - start_time ))" -ge "$TIMEOUT" ]; then
                echo -e "${RED}Error: Timeout waiting for nodes.${NC}" >&2
                exit 1
            fi
            ready_nodes=$(ray list nodes --address="${RAY_ADDRESS}" --format=json | python3 -c "import sys, json; print(len(json.load(sys.stdin)))")
            if [ "$ready_nodes" -ge "$SKYPILOT_NUM_NODES" ]; then
                break
            fi
            echo "Waiting... ($ready_nodes / $SKYPILOT_NUM_NODES nodes ready)"
            sleep 5
        done
        echo -e "${GREEN}All $SKYPILOT_NUM_NODES nodes have joined.${NC}"
    fi

    # Add sleep to after `ray start` to give ray enough time to daemonize
    sleep 5
else
    echo -e "${GREEN}Starting Ray worker node...${NC}"

    echo "Waiting for head node at ${RAY_ADDRESS}..."
    start_time=$(date +%s)
    while ! ray health-check --address="${RAY_ADDRESS}" &>/dev/null; do
        if [ "$(( $(date +%s) - start_time ))" -ge "$TIMEOUT" ]; then
            echo -e "${RED}Timed out waiting for head node. Exiting.${NC}" >&2
            exit 1
        fi
        echo "Head node not healthy yet. Retrying in 1s..."
        sleep 1
    done

    echo -e "${GREEN}Head node is healthy. Starting worker node...${NC}"
    ray start \
        --address="${RAY_ADDRESS}" \
        --disable-usage-stats

    echo -e "${GREEN}Worker node started successfully.${NC}"

    # Add sleep to after `ray start` to give ray enough time to daemonize
    sleep 5
fi
