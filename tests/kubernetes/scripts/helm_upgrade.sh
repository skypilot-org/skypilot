#!/bin/bash
set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Configuration
CLUSTER_NAME="skypilot-helm-test-cluster"
PROJECT_ID=$(gcloud config get-value project)
ZONE="us-central1-a"  # Replace with your preferred zone
NODE_COUNT=1
MACHINE_TYPE="e2-standard-8"  # 8 vCPU, 32GB memory
PACKAGE_NAME=${1:-"skypilot-nightly"}  # Accept package name as first argument, default to skypilot-nightly
CURRENT_VERSION=${2}  # The version to upgrade TO (required parameter)
NAMESPACE="skypilot"
RELEASE_NAME="skypilot"

# Global variables
API_ENDPOINT=""

# Validate required parameters
if [ -z "$CURRENT_VERSION" ]; then
    echo "Error: CURRENT_VERSION parameter is required"
    echo "Usage: $0 [PACKAGE_NAME] CURRENT_VERSION"
    echo "Example: $0 \"skypilot-nightly\" \"1.0.0.dev20250914\""
    echo "Example: $0 \"skypilot\" \"0.10.0\""
    echo ""
    exit 1
fi

echo "Using GCP Project ID: $PROJECT_ID"
echo "Testing Helm upgrade for package: $PACKAGE_NAME"
echo "Upgrading to version: $CURRENT_VERSION"

# Function to launch sky jobs (for first deployment)
launch_sky_jobs() {
    local version="$1"

    echo "=== Launching Sky Jobs for version $version ==="

    # Use the global API endpoint
    export SKYPILOT_API_SERVER_ENDPOINT="$API_ENDPOINT"
    echo "Set SKYPILOT_API_SERVER_ENDPOINT=$API_ENDPOINT"

    # Run sky status
    echo "Running sky status..."
    if ! sky status; then
        echo "❌ Error: Failed to run sky status"
        exit 1
    fi

    # Launch cluster job
    echo "Launching cluster job..."
    if ! sky launch -y -c test_helm_cluster --cpus 2+ --memory 4+ --infra aws "echo hi cluster job"; then
        echo "❌ Error: Failed to launch cluster job"
        exit 1
    fi

    # Launch managed job
    echo "Launching managed job..."
    if ! sky jobs launch -y -c test_helm_job --cpus 2+ --memory 4+ --infra aws "echo hi managed job"; then
        echo "❌ Error: Failed to launch managed job"
        exit 1
    fi

    echo "=== Sky Jobs launched for version $version ==="
}

# Function to check sky job logs (for second deployment)
check_sky_logs() {
    local version="$1"

    echo "=== Checking Sky Job Logs for version $version ==="

    # Use the global API endpoint
    export SKYPILOT_API_SERVER_ENDPOINT="$API_ENDPOINT"
    echo "Set SKYPILOT_API_SERVER_ENDPOINT=$API_ENDPOINT"

    # Check cluster job logs
    echo "Checking cluster job logs..."
    if ! cluster_logs=$(sky logs test_helm_cluster --no-follow 2>&1); then
        echo "❌ Error: Failed to retrieve cluster job logs"
        exit 1
    fi
    echo "Cluster job logs:"
    echo "$cluster_logs"
    if echo "$cluster_logs" | grep -q "hi cluster job"; then
        echo "✓ Cluster job log contains expected output"
    else
        echo "❌ Error: Cluster job log missing expected output"
        exit 1
    fi

    # Check managed job logs
    echo "Checking managed job logs..."
    if ! managed_logs=$(sky jobs logs 1 --no-follow 2>&1); then
        echo "❌ Error: Failed to retrieve managed job logs"
        exit 1
    fi
    echo "Managed job logs:"
    echo "$managed_logs"
    if echo "$managed_logs" | grep -q "hi managed job"; then
        echo "✓ Managed job log contains expected output"
    else
        echo "❌ Error: Managed job log missing expected output"
        exit 1
    fi

    echo "=== Sky Logs checked for version $version ==="
}

# Function to get previous version from helm repo
get_previous_version() {
    local current_ver="$1"
    local package_name="$2"

    echo "Querying Helm repository for available versions..."

    # Add and update helm repo if not already added
    helm repo add skypilot https://helm.skypilot.co || true
    helm repo update

    # Get all available versions for the package
    echo "Fetching available versions for package: $package_name"

    # For skypilot-nightly, we need to include --devel flag to get dev versions
    if [ "$package_name" = "skypilot-nightly" ]; then
        local versions=$(helm search repo skypilot/$package_name --versions --devel --output json | jq -r '.[].version' | sort -V)
    else
        # For skypilot (stable), we don't use --devel flag
        local versions=$(helm search repo skypilot/$package_name --versions --output json | jq -r '.[].version' | sort -V)
    fi

    if [ -z "$versions" ]; then
        echo "Error: No versions found for package $package_name"
        exit 1
    fi

    echo "Latest 10 versions:"
    echo "$versions" | tail -10

    # Convert current version format to match helm repo format
    # Convert 1.0.0.dev20250913 to 1.0.0-dev.20250913
    local helm_format_ver=$(echo "$current_ver" | sed 's/\.dev/-dev./')
    echo "Looking for version: $helm_format_ver"

    # Find the version that comes before the current version
    local previous_version=""
    local last_version=""

    while IFS= read -r version; do
        if [ "$version" = "$helm_format_ver" ]; then
            previous_version="$last_version"
            break
        fi
        last_version="$version"
    done <<< "$versions"

    if [ -z "$previous_version" ]; then
        echo "Error: Could not find a previous version for $current_ver (looking for $helm_format_ver)"
        echo "available versions:"
        echo "$versions"
        exit 1
    fi

    echo "Found previous version: $previous_version"
    PREVIOUS_VERSION="$previous_version"
}


# Cleanup function
cleanup() {
    echo "Cleaning up..."

    # Set environment variable for cleanup script
    if [ -n "$API_ENDPOINT" ]; then
        export SKYPILOT_API_SERVER_ENDPOINT="$API_ENDPOINT"
        echo "Set SKYPILOT_API_SERVER_ENDPOINT=$API_ENDPOINT for cleanup"
    fi

    # Clean up Sky resources
    echo "Cleaning up Sky resources..."
    if [ -f "$SCRIPT_DIR/../../smoke_tests/docker/stop_sky_resource.sh" ]; then
        echo "Running stop_sky_resource.sh..."
        bash "$SCRIPT_DIR/../../smoke_tests/docker/stop_sky_resource.sh"
    else
        echo "❌ Error: stop_sky_resource.sh not found at $SCRIPT_DIR/../../smoke_tests/docker/stop_sky_resource.sh"
    fi

    # Delete GKE cluster
    echo "Deleting GKE cluster: $CLUSTER_NAME"
    "$SCRIPT_DIR/delete_gke_cluster.sh" "$CLUSTER_NAME" "$PROJECT_ID" "$ZONE"
    echo "Cleanup complete"
}

# Set up trap
trap cleanup EXIT

# Get previous version
echo "Determining previous version..."
get_previous_version "$CURRENT_VERSION" "$PACKAGE_NAME"
echo "Previous version: $PREVIOUS_VERSION"


# Create GKE cluster
"$SCRIPT_DIR/create_gke_cluster.sh" "$CLUSTER_NAME" "$PROJECT_ID" "$ZONE" "$NODE_COUNT" "$MACHINE_TYPE"

# Deploy previous version
echo "Deploying previous version ($PREVIOUS_VERSION)..."
"$SCRIPT_DIR/helm_deploy_and_verify.sh" "$PACKAGE_NAME" "$PREVIOUS_VERSION" "$NAMESPACE" "$RELEASE_NAME" | tee /tmp/helm_output_prev.txt
deploy_output=$(cat /tmp/helm_output_prev.txt)

# Extract API endpoint from deployment output
API_ENDPOINT=$(echo "$deploy_output" | grep "API Endpoint:" | sed 's/API Endpoint: //')
if [ -z "$API_ENDPOINT" ]; then
    echo "❌ Error: Could not extract API endpoint from deployment output"
    exit 1
fi
echo "Extracted API endpoint: $API_ENDPOINT"

# Launch sky jobs for previous version
launch_sky_jobs "$PREVIOUS_VERSION"

# Upgrade to current version
echo "Upgrading to current version ($CURRENT_VERSION)..."
"$SCRIPT_DIR/helm_deploy_and_verify.sh" "$PACKAGE_NAME" "$CURRENT_VERSION" "$NAMESPACE" "$RELEASE_NAME" | tee /tmp/helm_output_curr.txt
deploy_output=$(cat /tmp/helm_output_curr.txt)

# Update API endpoint from deployment output
API_ENDPOINT=$(echo "$deploy_output" | grep "API Endpoint:" | sed 's/API Endpoint: //')
if [ -z "$API_ENDPOINT" ]; then
    echo "❌ Error: Could not extract API endpoint from deployment output"
    exit 1
fi
echo "Updated API endpoint: $API_ENDPOINT"

# Check sky job logs for current version
check_sky_logs "$CURRENT_VERSION"

# Verify upgrade in helm list
echo "Verifying upgrade in helm list..."
helm list -n $NAMESPACE

echo "=== Helm Upgrade Test Completed Successfully ==="
echo "Successfully upgraded from $PREVIOUS_VERSION to $CURRENT_VERSION"
