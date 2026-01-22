#!/bin/bash
# Test script for large production-scale performance testing
# This script sets up production-scale data and verifies performance metrics
#
# Usage:
#   ./test_large_production_performance.sh [--postgres] [--restart-api-server]
#
# Options:
#   --postgres              Create AWS RDS PostgreSQL database and restart sky api with DB connection
#   --restart-api-server    Restart sky api server with consolidation mode config

set -e  # Exit on error
set -o pipefail  # Exit on pipe failure

# Parse command line arguments
USE_POSTGRES=false
RESTART_API_SERVER=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --postgres)
            USE_POSTGRES=true
            shift
            ;;
        --restart-api-server)
            RESTART_API_SERVER=true
            shift
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Usage: $0 [--postgres] [--restart-api-server]" >&2
            exit 1
            ;;
    esac
done

# Configuration
ACTIVE_CLUSTER_NAME="scale-test-active"
TERMINATED_CLUSTER_NAME="scale-test-terminated"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INJECT_SCRIPT="${SCRIPT_DIR}/inject_production_scale_data.py"
CREATE_DB_SCRIPT="${SCRIPT_DIR}/create_aws_postgres_db.sh"
JOB_ID_FILE="/tmp/prod_test_job_id_$$"

# RDS configuration (instance name related to test case)
RDS_INSTANCE_ID="skypilot-large-production-test-db"
RDS_REGION="${AWS_REGION:-us-east-2}"
DB_SUBNET_GROUP_NAME="skypilot-test-subnet-group-${RDS_INSTANCE_ID}"
SKYPILOT_DB_CONNECTION_URI=""

# Cleanup function
cleanup() {
    echo "Cleaning up..."
    if [ -f "$JOB_ID_FILE" ]; then
        JOB_ID=$(cat "$JOB_ID_FILE" 2>/dev/null || echo "1")
        CLEANUP_ARGS=(--cleanup --managed-job-id "$JOB_ID")
        # Pass SQL URL if PostgreSQL was used
        if [ -n "$SKYPILOT_DB_CONNECTION_URI" ]; then
            CLEANUP_ARGS+=(--sql-url "$SKYPILOT_DB_CONNECTION_URI")
        fi
        python "$INJECT_SCRIPT" "${CLEANUP_ARGS[@]}" || true
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

    # Stop API server if it was restarted
    if [ "$RESTART_API_SERVER" = "true" ]; then
        echo "Stopping sky api server..."
        sky api stop || true
    fi

    # Cleanup RDS instance if it was created
    if [ "$USE_POSTGRES" = "true" ]; then
        echo "Cleaning up RDS instance..."
        if aws rds describe-db-instances --region "$RDS_REGION" --db-instance-identifier "$RDS_INSTANCE_ID" >/dev/null 2>&1; then
            echo "Deleting RDS instance: $RDS_INSTANCE_ID"
            aws rds delete-db-instance \
                --region "$RDS_REGION" \
                --db-instance-identifier "$RDS_INSTANCE_ID" \
                --skip-final-snapshot \
                2>/dev/null || true
        fi

        # Cleanup DB subnet group
        if [ -n "$DB_SUBNET_GROUP_NAME" ]; then
            echo "Cleaning up DB subnet group: $DB_SUBNET_GROUP_NAME"
            aws rds delete-db-subnet-group \
                --region "$RDS_REGION" \
                --db-subnet-group-name "$DB_SUBNET_GROUP_NAME" \
                2>/dev/null || true
        fi

        # Cleanup DB parameter group
        PARAM_GROUP_NAME="skypilot-test-pg-${RDS_INSTANCE_ID}"
        if aws rds describe-db-parameter-groups --region "$RDS_REGION" --db-parameter-group-name "$PARAM_GROUP_NAME" >/dev/null 2>&1; then
            echo "Cleaning up DB parameter group: $PARAM_GROUP_NAME"
            aws rds delete-db-parameter-group \
                --region "$RDS_REGION" \
                --db-parameter-group-name "$PARAM_GROUP_NAME" \
                2>/dev/null || true
        fi
    fi

    sky api stop || true
}

# Set trap to cleanup on exit
trap cleanup EXIT

echo "=========================================="
echo "Large Production Performance Test"
echo "=========================================="

# Step 0: Create PostgreSQL database and/or restart API server if requested
# If USE_POSTGRES is true, automatically set RESTART_API_SERVER to true
if [ "$USE_POSTGRES" = "true" ]; then
    RESTART_API_SERVER=true
fi

if [ "$RESTART_API_SERVER" = "true" ]; then
    echo "Step 0: Setting up API server..."

    # Create PostgreSQL database if requested
    if [ "$USE_POSTGRES" = "true" ]; then
        echo "Creating AWS RDS PostgreSQL database..."
        if [ ! -f "$CREATE_DB_SCRIPT" ]; then
            echo "ERROR: Database creation script not found: $CREATE_DB_SCRIPT" >&2
            exit 1
        fi

        # Create the database and get connection URI
        export RDS_INSTANCE_ID
        # Run script - stderr shows progress, stdout (URI) is captured
        SKYPILOT_DB_CONNECTION_URI=$(bash "$CREATE_DB_SCRIPT" | grep -oE 'postgresql://[^[:space:]]+' | head -n1)
        if [ -z "$SKYPILOT_DB_CONNECTION_URI" ]; then
            echo "ERROR: Failed to extract PostgreSQL connection URI from script output" >&2
            exit 1
        fi
        # Trim any whitespace from the URI
        SKYPILOT_DB_CONNECTION_URI=$(echo "$SKYPILOT_DB_CONNECTION_URI" | tr -d '\n\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        export SKYPILOT_DB_CONNECTION_URI
        echo "SQL Connection URI: $SKYPILOT_DB_CONNECTION_URI" >&2
        echo "✓ RDS database created"
    fi

    # Set config for consolidation mode
    export SKYPILOT_GLOBAL_CONFIG=tests/test_yamls/consolidation_mode_config.yaml
    echo "✓ Config set for consolidation mode"

    # Restart API server with appropriate environment variables
    echo "Restarting sky api..."
    sky api stop || true

    # Build the command with environment variables
    if [ -n "$SKYPILOT_DB_CONNECTION_URI" ]; then
        echo "Starting sky api with SQL Connection URI: $SKYPILOT_DB_CONNECTION_URI" >&2
        SKYPILOT_DB_CONNECTION_URI="$SKYPILOT_DB_CONNECTION_URI" sky api start
        echo "✓ Sky api restarted with database connection and consolidation mode config"
    else
        sky api start
        echo "✓ Sky api restarted with consolidation mode config"
    fi
fi

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
INJECT_ARGS=(
    --active-cluster "$ACTIVE_CLUSTER_NAME"
    --terminated-cluster "$TERMINATED_CLUSTER_NAME"
    --managed-job-id "$JOB_ID"
)
if [ -n "$SKYPILOT_DB_CONNECTION_URI" ]; then
    INJECT_ARGS+=(--sql-url "$SKYPILOT_DB_CONNECTION_URI")
    echo "Using remote PostgreSQL database for data injection"
fi
python "$INJECT_SCRIPT" "${INJECT_ARGS[@]}"

# Step 5: Test sky status performance
echo "Step 5: Testing sky status performance..."
echo "Expected: Show '12501 RUNNING' or '12501 STARTING' or '12501 PENDING' and finish within 18 seconds"
time_start=$(date +%s)
STATUS_OUTPUT=$(timeout 60 sky status 2>&1 || true)
time_end=$(date +%s)
duration=$((time_end - time_start))

echo "$STATUS_OUTPUT"
echo "Duration: ${duration}s"

if ! echo "$STATUS_OUTPUT" | grep -qE "12501.*(RUNNING|STARTING|PENDING)"; then
    echo "ERROR: sky status output does not contain '12501 RUNNING' or '12501 STARTING' or '12501 PENDING'"
    exit 1
fi

if [ $duration -gt 18 ]; then
    echo "ERROR: sky status took ${duration}s, expected <= 18s"
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
echo "Expected: Last job ID 1 and finish within 20 seconds"
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

if [ $duration -gt 20 ]; then
    echo "ERROR: sky jobs queue --all took ${duration}s, expected <= 20s"
    exit 1
fi

echo "✓ sky jobs queue --all test passed (${duration}s)"

# Step 8: Do a minimal sky launch to ensure API server is running
echo "Step 8: Performing minimal sky launch to ensure API server is running..."
MINIMAL_CLUSTER_NAME="scale-test-minimal-$$"
sky launch --infra k8s -c "$MINIMAL_CLUSTER_NAME" -y "echo 'minimal test cluster'"
# Get logs and verify the echo content appears
LOGS_OUTPUT=$(sky logs "$MINIMAL_CLUSTER_NAME" --no-follow 2>&1)
if ! echo "$LOGS_OUTPUT" | grep -q "minimal test cluster"; then
    echo "ERROR: Minimal sky launch logs do not contain expected echo content 'minimal test cluster'"
    echo "Logs output: $LOGS_OUTPUT"
    exit 1
fi
echo "✓ Minimal sky launch verified - found expected echo content in logs"
# Clean up immediately
sky down "$MINIMAL_CLUSTER_NAME" -y || true

# Step 9: Build dashboard before verification
echo "Step 9: Building dashboard..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKYPILOT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
DASHBOARD_DIR="${SKYPILOT_ROOT}/sky/dashboard"

if [ -d "$DASHBOARD_DIR" ]; then
    if command -v npm &> /dev/null; then
        echo "Installing dashboard dependencies..."
        npm --prefix "$DASHBOARD_DIR" install
        echo "Building dashboard..."
        npm --prefix "$DASHBOARD_DIR" run build
        echo "✓ Dashboard built successfully"
    else
        echo "ERROR: npm not found, cannot build dashboard. Please install Node.js and npm."
        exit 1
    fi
else
    echo "ERROR: Dashboard directory not found at $DASHBOARD_DIR"
    exit 1
fi

# Step 10: Verify dashboard pages in browser
echo "Step 10: Verifying dashboard pages in browser..."
VERIFY_SCRIPT="${SCRIPT_DIR}/verify_dashboard_browser.py"

# Get API server endpoint
# Try to get from environment variable first, then try sky api info, then default
API_ENDPOINT="${SKYPILOT_API_SERVER_URL:-}"
if [ -z "$API_ENDPOINT" ]; then
    # Try to get from sky api info command
    API_INFO=$(sky api info 2>/dev/null || echo "")
    if echo "$API_INFO" | grep -q "endpoint"; then
        API_ENDPOINT=$(echo "$API_INFO" | grep -i "endpoint" | head -n1 | sed -E 's/.*[Ee]ndpoint[^:]*:\s*//' | tr -d '[:space:]')
    fi
fi
# Fallback to default if still empty
if [ -z "$API_ENDPOINT" ]; then
    API_ENDPOINT="http://localhost:46580"
fi

echo "Using API endpoint: $API_ENDPOINT"

if [ -f "$VERIFY_SCRIPT" ]; then
    python3 "$VERIFY_SCRIPT" --endpoint "$API_ENDPOINT"
else
    echo "ERROR: Dashboard verification script not found at $VERIFY_SCRIPT"
    exit 1
fi

echo ""
echo "=========================================="
echo "All performance tests passed!"
echo "=========================================="

# Cleanup will run via trap on exit
