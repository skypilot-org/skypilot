#!/bin/bash
set -e

# Configuration
CLUSTER_NAME="skypilot-helm-test-cluster"
PROJECT_ID=$(gcloud config get-value project)
ZONE="us-central1-a"  # Replace with your preferred zone
NODE_COUNT=2
MACHINE_TYPE="e2-standard-8"  # 8 vCPU, 32GB memory
PACKAGE_NAME=${1:-"skypilot-nightly"}  # Accept package name as first argument, default to skypilot-nightly
HELM_VERSION=${2:-"latest"}  # Accept version as second argument, default to latest

# Cleanup function
cleanup() {
    echo "Cleaning up..."
    echo "Deleting GKE cluster: $CLUSTER_NAME"
    gcloud container clusters delete $CLUSTER_NAME \
        --project=$PROJECT_ID \
        --zone=$ZONE \
        --quiet || true
    echo "Cleanup complete"
}

# Set up trap
trap cleanup EXIT

echo "Using GCP Project ID: $PROJECT_ID"
echo "Installing SkyPilot Helm chart version: $HELM_VERSION"

# Create GKE cluster
echo "Creating GKE cluster..."
gcloud container clusters create $CLUSTER_NAME \
    --project=$PROJECT_ID \
    --zone=$ZONE \
    --num-nodes=$NODE_COUNT \
    --machine-type=$MACHINE_TYPE \
    --enable-ip-alias \
    --enable-autoscaling \
    --min-nodes=1 \
    --max-nodes=3

# Get credentials
echo "Getting cluster credentials..."
gcloud container clusters get-credentials $CLUSTER_NAME --zone=$ZONE --project=$PROJECT_ID

# Add SkyPilot Helm repository
echo "Adding SkyPilot Helm repository..."
helm repo add skypilot https://helm.skypilot.co
helm repo update

# Set up namespace and release name
NAMESPACE=skypilot
RELEASE_NAME=skypilot
WEB_USERNAME=skypilot
# Generate a random 16-character password using /dev/urandom (macOS compatible)
WEB_PASSWORD=$(LC_ALL=C tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c 16)

# Create auth string
AUTH_STRING=$(htpasswd -nb $WEB_USERNAME $WEB_PASSWORD)

# Deploy SkyPilot API server
echo "Deploying SkyPilot API server..."

# Set version-specific flag
if [ "$HELM_VERSION" = "latest" ]; then
    extra_flag="--devel"
else
    # Convert PEP440 version to SemVer if needed (e.g., 1.0.0.dev20250609 -> 1.0.0-dev.20250609)
    SEMVER_VERSION=$(echo "$HELM_VERSION" | sed -E 's/([0-9]+\.[0-9]+\.[0-9]+)\.dev([0-9]+)/\1-dev.\2/')
    extra_flag="--version $SEMVER_VERSION"
fi

helm upgrade --install $RELEASE_NAME skypilot/$PACKAGE_NAME $extra_flag \
    --namespace $NAMESPACE \
    --create-namespace \
    --set ingress.authCredentials=$AUTH_STRING

# Wait for pods to be ready
echo "Waiting for pods to be ready..."
echo "Checking pod status..."
kubectl get pods -n $NAMESPACE

# Wait for pods with increased timeout and better error handling
if ! kubectl wait --namespace $NAMESPACE \
    --for=condition=ready pod \
    --selector=app=${RELEASE_NAME}-api \
    --timeout=600s; then
    echo "Warning: Pod readiness check timed out. Checking pod status for debugging..."
    kubectl describe pods -n $NAMESPACE -l app=${RELEASE_NAME}-api
    kubectl logs -n $NAMESPACE -l app=${RELEASE_NAME}-api
    exit 1
fi

# Get the API server URL
echo "Getting API server URL..."
HOST=$(kubectl get svc ${RELEASE_NAME}-ingress-nginx-controller --namespace $NAMESPACE -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
ENDPOINT=http://${WEB_USERNAME}:${WEB_PASSWORD}@${HOST}

echo "API server endpoint: $ENDPOINT"

# Test the API server with retry logic
echo "Testing API server health endpoint..."
echo "Endpoint: $ENDPOINT/api/health"
MAX_RETRIES=10  # 5 minutes with 30 second intervals
RETRY_INTERVAL=30
RETRY_COUNT=0

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    echo "Attempt $((RETRY_COUNT + 1))/$MAX_RETRIES: Testing health endpoint..."
    HEALTH_RESPONSE=$(curl -s ${ENDPOINT}/api/health)
    echo "Health response: $HEALTH_RESPONSE"

    # Check if response is valid JSON (not HTML error page)
    if echo "$HEALTH_RESPONSE" | jq . >/dev/null 2>&1; then
        echo "API server is responding with valid JSON!"
        break
    else
        echo "API server not ready yet (received HTML error page). Waiting ${RETRY_INTERVAL} seconds..."
        if [ $RETRY_COUNT -lt $((MAX_RETRIES - 1)) ]; then
            sleep $RETRY_INTERVAL
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
    fi
done

if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
    echo "Error: API server failed to become ready after $((MAX_RETRIES * RETRY_INTERVAL)) seconds"
    exit 1
fi

# Extract version from response and verify it matches (only if not using 'latest')
RETURNED_VERSION=$(echo $HEALTH_RESPONSE | jq -r '.version')

# Verify that we got a valid version string
if [ -z "$RETURNED_VERSION" ] || [ ${#RETURNED_VERSION} -le 1 ]; then
    echo "Error: Invalid version returned from API. Got: '$RETURNED_VERSION'"
    exit 1
fi

if [ "$HELM_VERSION" != "latest" ]; then
    if [ "$RETURNED_VERSION" != "$HELM_VERSION" ]; then
        echo "Error: Version mismatch! Expected: $HELM_VERSION, Got: $RETURNED_VERSION"
        exit 1
    fi
    echo "Version verification successful! Expected version $HELM_VERSION matches returned version $RETURNED_VERSION"
else
    echo "Using latest version. Skipping version verification. Deployed version: $RETURNED_VERSION"
fi
