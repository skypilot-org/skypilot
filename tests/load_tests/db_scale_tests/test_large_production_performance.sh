#!/bin/bash
# Test script for large production-scale performance testing
# This script sets up production-scale data and verifies performance metrics

set -e  # Exit on error
set -o pipefail  # Exit on pipe failure

# Configuration
ACTIVE_CLUSTER_NAME="scale-test-active"
TERMINATED_CLUSTER_NAME="scale-test-terminated"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INJECT_SCRIPT="${SCRIPT_DIR}/inject_production_scale_data.py"
JOB_ID_FILE="/tmp/prod_test_job_id_$$"

# Cleanup function
cleanup() {
    echo "Cleaning up..."
    if [ -f "$JOB_ID_FILE" ]; then
        JOB_ID=$(cat "$JOB_ID_FILE" 2>/dev/null || echo "1")
        python "$INJECT_SCRIPT" --cleanup --managed-job-id "$JOB_ID" || true
        rm -f "$JOB_ID_FILE"
    fi
    sky jobs cancel -a -y || true
    sky down -a -y || true

    # Wait for all managed jobs to terminate
    echo "Waiting for managed jobs to terminate..."
    while true; do
        STATUS_OUTPUT=$(sky status 2>/dev/null || echo "")
        # Check if there are any managed jobs by looking for job ID pattern (number at start of line)
        if echo "$STATUS_OUTPUT" | grep -qE "^[0-9]+\s+"; then
            echo "$STATUS_OUTPUT"
            echo "Waiting for termination..."
            sleep 10
        else
            # No job IDs found, exit the loop
            echo "$STATUS_OUTPUT"
            echo "All managed jobs have terminated successfully."
            break
        fi
    done
}

# Set trap to cleanup on exit
trap cleanup EXIT

echo "=========================================="
echo "Large Production Performance Test"
echo "=========================================="

# Step 1: Create sample terminated cluster
echo "Step 1: Creating sample terminated cluster..."
sky launch --infra k8s -c "$TERMINATED_CLUSTER_NAME" -y "echo 'terminated cluster'"
sky down "$TERMINATED_CLUSTER_NAME" -y

# Step 2: Create sample active cluster
echo "Step 2: Creating sample active cluster..."
sky launch --infra k8s -c "$ACTIVE_CLUSTER_NAME" -y "echo 'active cluster'"

# Step 3: Create sample managed job and capture job ID
echo "Step 3: Creating sample managed job..."
sky jobs launch --infra k8s "sleep 10000000" -y -d
sleep 2  # Wait a moment for job to be registered
JOB_ID=$(sky jobs queue | grep "sky-cmd" | head -n1 | awk '{print $1}')
echo "Template job ID: $JOB_ID"
echo "$JOB_ID" > "$JOB_ID_FILE"

# Step 4: Inject production-scale data
echo "Step 4: Injecting production-scale data..."
python "$INJECT_SCRIPT" \
    --active-cluster "$ACTIVE_CLUSTER_NAME" \
    --terminated-cluster "$TERMINATED_CLUSTER_NAME" \
    --managed-job-id "$JOB_ID"

# Step 5: Test sky status performance
echo "Step 5: Testing sky status performance..."
echo "Expected: Show '12501 RUNNING' or '12501 STARTING' and finish within 11 seconds"
time_start=$(date +%s)
STATUS_OUTPUT=$(timeout 60 sky status 2>&1 || true)
time_end=$(date +%s)
duration=$((time_end - time_start))

echo "$STATUS_OUTPUT"
echo "Duration: ${duration}s"

if ! echo "$STATUS_OUTPUT" | grep -qE "12501.*(RUNNING|STARTING)"; then
    echo "ERROR: sky status output does not contain '12501 RUNNING' or '12501 STARTING'"
    exit 1
fi

if [ $duration -gt 11 ]; then
    echo "ERROR: sky status took ${duration}s, expected <= 11s"
    exit 1
fi

echo "✓ sky status test passed (${duration}s)"

# Step 6: Test sky jobs queue performance
echo "Step 6: Testing sky jobs queue performance..."
echo "Expected: Last job ID >= 12452 and finish within 10 seconds"
time_start=$(date +%s)
QUEUE_OUTPUT=$(timeout 60 sky jobs queue 2>&1 || true)
time_end=$(date +%s)
duration=$((time_end - time_start))

echo "Last 50 lines of output:"
echo "$QUEUE_OUTPUT" | tail -n 50
echo "Duration: ${duration}s"

LAST_JOB_ID=$(echo "$QUEUE_OUTPUT" | grep "sky-cmd" | tail -n1 | awk '{print $1}')
echo "Last job ID (in latest 50): $LAST_JOB_ID"

if [ "$LAST_JOB_ID" -lt 12452 ]; then
    echo "ERROR: Expected last job ID >= 12452, got $LAST_JOB_ID"
    exit 1
fi

if [ $duration -gt 10 ]; then
    echo "ERROR: sky jobs queue took ${duration}s, expected <= 10s"
    exit 1
fi

echo "✓ sky jobs queue test passed (${duration}s)"

# Step 7: Test sky jobs queue --all performance
echo "Step 7: Testing sky jobs queue --all performance..."
echo "Expected: Last job ID 1 and finish within 30 seconds"
time_start=$(date +%s)
QUEUE_ALL_OUTPUT=$(timeout 60 sky jobs queue --all 2>&1 || true)
time_end=$(date +%s)
duration=$((time_end - time_start))

echo "Last 50 lines of output:"
echo "$QUEUE_ALL_OUTPUT" | tail -n 50
echo "Duration: ${duration}s"

LAST_JOB_ID_ALL=$(echo "$QUEUE_ALL_OUTPUT" | grep "sky-cmd" | tail -n1 | awk '{print $1}')
echo "Last job ID (--all): $LAST_JOB_ID_ALL"

if [ "$LAST_JOB_ID_ALL" != "1" ]; then
    echo "ERROR: Expected last job ID 1, got $LAST_JOB_ID_ALL"
    exit 1
fi

if [ $duration -gt 30 ]; then
    echo "ERROR: sky jobs queue --all took ${duration}s, expected <= 30s"
    exit 1
fi

echo "✓ sky jobs queue --all test passed (${duration}s)"

echo ""
echo "=========================================="
echo "All performance tests passed!"
echo "=========================================="

# Cleanup will run via trap on exit
