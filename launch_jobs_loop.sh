#!/bin/bash

# Script to launch SkyPilot jobs in a loop
# Runs: sky jobs launch --infra k8s --num-nodes 2 --async -y "sleep 100000"
# Number of iterations: 100

echo "Starting to launch 100 jobs..."
echo "Start time: $(date)"
echo "================================"

for i in $(seq 1 1000); do
    echo ""
    echo "Launching job $i/1000 at $(date)"
    sky jobs launch --infra k8s --num-nodes 2 --async -y "echo doggydog"

    if [ $? -eq 0 ]; then
        echo "✓ Job $i launched successfully"
    else
        echo "✗ Job $i failed with exit code $?"
    fi
done

echo ""
echo "================================"
echo "Completed launching 100 jobs"
echo "End time: $(date)"
