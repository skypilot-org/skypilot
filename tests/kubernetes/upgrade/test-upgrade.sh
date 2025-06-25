#!/bin/bash

set -euo pipefail

# Assume the release is installed
SERVER_URL=${1}
NAMESPACE=${2:-skypilot}
RELEASE_NAME=${3:-skypilot}

if [ -z "$SERVER_URL" ]; then
    echo "Server URL not provided"
    exit 1
fi

helm ls -n $NAMESPACE | grep $RELEASE_NAME || (echo "Release $RELEASE_NAME not found in namespace $NAMESPACE" && exit 1)

echo "Running upgrade test with server URL: $SERVER_URL"

CLUSTER_NAME="test-upgrade"

sky api login -e $SERVER_URL
log_file=$(mktemp)
sky launch -c $CLUSTER_NAME -y --cpus 1+ 'for i in {1..300}; do echo "count: $i" && sleep 1; done' --infra kubernetes > $log_file 2>&1 &
tail_pid=$!
echo "Launch and tailing log to $log_file, PID: $tail_pid"

timeout=120
elapsed=0
while [ $elapsed -lt $timeout ]; do
    if grep -q "count: 1" "$log_file"; then
        break
    fi
    sleep 1
    elapsed=$((elapsed + 1))
done

if [ $elapsed -ge $timeout ]; then
    echo "Timeout wait the log tailing start"
    exit 1
fi

echo "Triggering rolling update"
timestamp=$(date +%s)
helm upgrade $RELEASE_NAME charts/skypilot \
    --namespace $NAMESPACE \
    --reuse-values \
    --set apiService.annotations.restart="at$timestamp"

# wait curl $SERVER_URL/api/info returns 503, and run concurrent sky status command in background, capture their pid, wait them exit and verify the exit code should be 0

echo "Wait rolling upgrade dispatched"
timeout=60
elapsed=0
while [ $elapsed -lt $timeout ]; do
    http_code=$(curl -s -o /dev/null -w "%{http_code}" "$SERVER_URL/api/status" || echo "000")
    if [ "$http_code" = "503" ]; then
        break
    fi
    sleep 1
    elapsed=$((elapsed + 1))
done

if [ $elapsed -ge $timeout ]; then
    echo "Timeout wait rolling upgrade dispatched"
    exit 1
fi

sky_pids=($tail_pid)
sky status -c $CLUSTER_NAME > /dev/null 2>&1 &
sky_pids+=($!)
sky launch --dryrun -y > /dev/null 2>&1 &
sky_pids+=($!)
sky jobs queue > /dev/null 2>&1 &
sky_pids+=($!)

failed_jobs=0
for pid in "${sky_pids[@]}"; do
    if wait $pid; then
        echo "Command with PID $pid completed successfully"
    else
        echo "Command with PID $pid failed"
    fi
done

s=$(cat $log_file)

echo $s | grep "count: 1" | wc -l | grep 1 || (echo "Incorrect log tailing, refer to $log_file for details" && exit 1)
echo $s | grep "count: 300" | wc -l | grep 1 || (echo "Incorrect log tailing, refer to $log_file for details" && exit 1)

sky down $CLUSTER_NAME -y
