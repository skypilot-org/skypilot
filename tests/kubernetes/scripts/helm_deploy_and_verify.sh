#!/bin/bash
set -e

# Configuration
PACKAGE_NAME=${1:-"skypilot-nightly"}
HELM_VERSION=${2:-"latest"}
NAMESPACE=${3:-"skypilot"}
RELEASE_NAME=${4:-"skypilot"}
WEB_USERNAME=${5:-"skypilot"}

echo "Deploying SkyPilot Helm chart..."
echo "Package Name: $PACKAGE_NAME"
echo "Helm Version: $HELM_VERSION"
echo "Namespace: $NAMESPACE"
echo "Release Name: $RELEASE_NAME"

# Add SkyPilot Helm repository
echo "Adding SkyPilot Helm repository..."
helm repo add skypilot https://helm.skypilot.co
helm repo update

# Generate a random 16-character password using /dev/urandom (macOS compatible)
WEB_PASSWORD=$(LC_ALL=C tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c 16)

# Create auth string (Apache MD5 apr1); pass raw via --set-string (no escaping)
AUTH_STRING=$(htpasswd -nb "$WEB_USERNAME" "$WEB_PASSWORD")

# Create namespace first if it doesn't exist
echo "Creating namespace $NAMESPACE if it doesn't exist..."
kubectl create namespace $NAMESPACE --dry-run=client -o yaml | kubectl apply -f -

# Setup AWS credentials from default profile
echo "Setting up AWS credentials from default profile..."
AWS_CREDENTIALS_FILE="$HOME/.aws/credentials"

if [ -f "$AWS_CREDENTIALS_FILE" ]; then
    echo "Found AWS credentials file, extracting from default profile..."
    AWS_ACCESS_KEY_ID=$(grep -A 10 "^\[default\]" "$AWS_CREDENTIALS_FILE" | grep "^aws_access_key_id" | head -1 | sed 's/.*= *//')
    AWS_SECRET_ACCESS_KEY=$(grep -A 10 "^\[default\]" "$AWS_CREDENTIALS_FILE" | grep "^aws_secret_access_key" | head -1 | sed 's/.*= *//')

    if [ -n "$AWS_ACCESS_KEY_ID" ] && [ -n "$AWS_SECRET_ACCESS_KEY" ]; then
        echo "Creating AWS credentials secret..."
        kubectl create secret generic aws-credentials \
          --namespace $NAMESPACE \
          --from-literal=aws_access_key_id="${AWS_ACCESS_KEY_ID}" \
          --from-literal=aws_secret_access_key="${AWS_SECRET_ACCESS_KEY}" \
          --dry-run=client -o yaml | kubectl apply -f -

        echo "✓ AWS credentials secret created"
        AWS_CREDENTIALS_ENABLED=true
    else
        echo "❌ Error: Could not extract AWS credentials from default profile"
        echo "   Please ensure your ~/.aws/credentials file has a [default] section with:"
        echo "   aws_access_key_id = YOUR_ACCESS_KEY"
        echo "   aws_secret_access_key = YOUR_SECRET_KEY"
        exit 1
    fi
else
    echo "❌ Error: AWS credentials file not found at: $AWS_CREDENTIALS_FILE"
    echo "   Please create the file with your AWS credentials in the [default] section"
    exit 1
fi

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

# Prepare Helm command arguments
HELM_ARGS=(
    --namespace $NAMESPACE
    --create-namespace
    --set-string ingress.authCredentials="$AUTH_STRING"
)

# Add AWS credentials settings if enabled
if [ "$AWS_CREDENTIALS_ENABLED" = true ]; then
    HELM_ARGS+=(
        --set awsCredentials.enabled=true
        --set awsCredentials.awsSecretName=aws-credentials
    )
    echo "✓ AWS credentials will be enabled in Helm deployment"
fi

echo "Executing: helm upgrade --install $RELEASE_NAME skypilot/$PACKAGE_NAME $extra_flag ${HELM_ARGS[*]}"
helm upgrade --install $RELEASE_NAME skypilot/$PACKAGE_NAME $extra_flag "${HELM_ARGS[@]}"

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

# Wait for LoadBalancer to have an ingress address
echo "Waiting for LoadBalancer external address..."
for i in {1..40}; do
    ingress_obj=$(kubectl get svc ${RELEASE_NAME}-ingress-nginx-controller --namespace $NAMESPACE -o json | jq -r '.status.loadBalancer.ingress[0] // empty') || true
    if [ -n "$ingress_obj" ] && [ "$ingress_obj" != "null" ]; then
        break
    fi
    echo "... still waiting for external address (attempt $i)"
    sleep 15
done

# Prefer hostname (EKS) and fallback to IP
HOST=$(kubectl get svc ${RELEASE_NAME}-ingress-nginx-controller --namespace $NAMESPACE -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
if [ -z "$HOST" ]; then
    HOST=$(kubectl get svc ${RELEASE_NAME}-ingress-nginx-controller --namespace $NAMESPACE -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
fi

if [ -z "$HOST" ]; then
    echo "Error: LoadBalancer external address not assigned"
    kubectl get svc ${RELEASE_NAME}-ingress-nginx-controller -n $NAMESPACE -o wide | cat
    kubectl describe svc ${RELEASE_NAME}-ingress-nginx-controller -n $NAMESPACE | cat
    exit 1
fi

ENDPOINT=http://${WEB_USERNAME}:${WEB_PASSWORD}@${HOST}

echo "API server endpoint: $ENDPOINT"

# Test the API server with retry logic
echo "Testing API server health endpoint..."
echo "Endpoint: http://$WEB_USERNAME:$WEB_PASSWORD@$HOST/api/health"
MAX_RETRIES=10  # 5 minutes with 30 second intervals
RETRY_INTERVAL=30
RETRY_COUNT=0
HEALTH_RESPONSE=""

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    echo "Attempt $((RETRY_COUNT + 1))/$MAX_RETRIES: Testing health endpoint..."
    HEALTH_RESPONSE=$(curl -s -u "$WEB_USERNAME:$WEB_PASSWORD" "http://$HOST/api/health" 2>&1)
    echo "Health response: '$HEALTH_RESPONSE'"
    echo "Response length: ${#HEALTH_RESPONSE} characters"

    # Check if response is valid JSON with proper version
    if [ -n "$HEALTH_RESPONSE" ] && echo "$HEALTH_RESPONSE" | jq . >/dev/null 2>&1; then
        RETURNED_VERSION=$(echo "$HEALTH_RESPONSE" | jq -r '.version')
        if [ -n "$RETURNED_VERSION" ] && [ "$RETURNED_VERSION" != "null" ] && [ ${#RETURNED_VERSION} -gt 1 ]; then
            echo "API server is ready! Version: $RETURNED_VERSION"
            break
        fi
    fi

    echo "API server not ready yet. Waiting ${RETRY_INTERVAL} seconds..."

    if [ $RETRY_COUNT -lt $((MAX_RETRIES - 1)) ]; then
        sleep $RETRY_INTERVAL
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
done

if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
    echo "Error: API server failed to become ready after $((MAX_RETRIES * RETRY_INTERVAL)) seconds"
    echo "Final health response: '$HEALTH_RESPONSE'"
    exit 1
fi

if [ "$HELM_VERSION" != "latest" ]; then
    # Convert HELM_VERSION to match the returned version format (dash to dot)
    EXPECTED_VERSION=$(echo "$HELM_VERSION" | sed 's/-dev\./\.dev/')

    if [ "$RETURNED_VERSION" != "$EXPECTED_VERSION" ]; then
        echo "Error: Version mismatch! Expected: $HELM_VERSION, Got: $RETURNED_VERSION"
        exit 1
    fi
    echo "Version verification successful! Expected version $HELM_VERSION matches returned version $RETURNED_VERSION"
else
    echo "Using latest version. Skipping version verification. Deployed version: $RETURNED_VERSION"
fi

echo "Helm deployment and verification completed successfully!"
echo "API Endpoint: $ENDPOINT"
echo "Username: $WEB_USERNAME"
echo "Password: $WEB_PASSWORD"
