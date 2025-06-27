#!/bin/bash

# This script deploys SkyPilot with OAuth2 Proxy (Okta) authentication on a kind cluster
# created by 'sky local up'. It includes specific fixes for kind cluster networking issues.
#
# Key modifications for kind cluster compatibility:
# 1. Disables ingress-nginx subchart to avoid conflicts with the existing nginx from 'sky local up'
# 2. Patches OAuth2 proxy ingress annotations to use internal cluster service URLs
# 3. Uses localhost with NodePort for accessing the API server
# 4. Supports automated login for testing (when test credentials are provided)
#
# Prerequisites:
# - Run 'sky local up' first to create the kind cluster with nginx ingress
# - Set required environment variables: OKTA_CLIENT_ID, OKTA_CLIENT_SECRET, OKTA_TEST_USERNAME, OKTA_TEST_PASSWORD
#
# Usage:
#   OKTA_CLIENT_ID=your_client_id OKTA_CLIENT_SECRET=your_secret \
#   OKTA_TEST_USERNAME=test@example.com OKTA_TEST_PASSWORD=pass \
#   ./helm_okta.sh
#
#   All four environment variables are required:
#   - OKTA_CLIENT_ID: OAuth application client ID
#   - OKTA_CLIENT_SECRET: OAuth application client secret
#   - OKTA_TEST_USERNAME: Test user email/username
#   - OKTA_TEST_PASSWORD: Test user password

NAMESPACE=skypilot
NODEPORT=30082
HTTPS_NODEPORT=30100

# Cleanup function to delete namespace and resources
cleanup() {
    echo ""
    echo "🧹 Cleaning up resources..."
    if kubectl get namespace $NAMESPACE >/dev/null 2>&1; then
        echo "Deleting namespace $NAMESPACE..."
        kubectl delete namespace $NAMESPACE --ignore-not-found=true
        echo "✅ Namespace $NAMESPACE deleted"
    else
        echo "Namespace $NAMESPACE does not exist, skipping deletion"
    fi
    echo "🧹 Cleanup complete"
}

# Set up trap to call cleanup function on script exit
trap cleanup EXIT

# Assert all required environment variables are provided
if [[ -z "$OKTA_CLIENT_ID" ]]; then
    echo "❌ OKTA_CLIENT_ID is required"
    exit 1
fi

if [[ -z "$OKTA_CLIENT_SECRET" ]]; then
    echo "❌ OKTA_CLIENT_SECRET is required"
    exit 1
fi

if [[ -z "$OKTA_TEST_USERNAME" ]]; then
    echo "❌ OKTA_TEST_USERNAME is required"
    exit 1
fi

if [[ -z "$OKTA_TEST_PASSWORD" ]]; then
    echo "❌ OKTA_TEST_PASSWORD is required"
    exit 1
fi

echo "Using OAuth client ID: $OKTA_CLIENT_ID"
echo "Using test credentials for user: $OKTA_TEST_USERNAME"


helm repo add skypilot https://helm.skypilot.co
helm repo update

# Verify that nginx ingress controller is already running (installed by sky local up)
echo "Verifying nginx ingress controller is running..."
if ! kubectl get pods -n ingress-nginx -l app.kubernetes.io/component=controller --no-headers | grep -q "Running"; then
    echo "ERROR: nginx ingress controller is not running. Please run 'sky local up' first."
    exit 1
fi
echo "nginx ingress controller is running ✓"

# Patch the nginx ingress controller service to use NodePort $NODEPORT
echo "Configuring nginx ingress controller to use NodePort $NODEPORT..."
kubectl patch svc ingress-nginx-controller -n ingress-nginx -p '{
    "spec": {
        "type": "NodePort",
        "ports": [
            {
                "name": "http",
                "port": 80,
                "protocol": "TCP",
                "targetPort": "http",
                "nodePort": '$NODEPORT'
            },
            {
                "name": "https",
                "port": 443,
                "protocol": "TCP",
                "targetPort": "https",
                "nodePort": '$HTTPS_NODEPORT'
            }
        ]
    }
}'
echo "nginx ingress controller configured for NodePort $NODEPORT ✓"

# Get the image name from the Helm chart
echo "Getting image name from Helm chart..."
FULL_IMAGE_NAME=$(helm show values skypilot/skypilot-nightly --devel | grep "^  image:" | awk '{print $2}')

echo "Loading image $FULL_IMAGE_NAME into kind cluster..."
# Pull the image first (in case it's not available locally)
docker pull $FULL_IMAGE_NAME
# Load the image into kind cluster
kind load docker-image $FULL_IMAGE_NAME --name skypilot

# Install the Skypilot Helm chart with the OAuth2 Proxy
# Disable the ingress-nginx subchart since sky local up already installed nginx
helm upgrade -n $NAMESPACE --install skypilot skypilot/skypilot-nightly --devel \
  --create-namespace \
  --set ingress.oauth2-proxy.enabled=true \
  --set ingress.oauth2-proxy.oidc-issuer-url="https://trial-3279999.okta.com" \
  --set ingress.oauth2-proxy.client-id="$OKTA_CLIENT_ID" \
  --set ingress.oauth2-proxy.client-secret="$OKTA_CLIENT_SECRET" \
  --set imagePullPolicy=IfNotPresent \
  --set ingress-nginx.enabled=false

# Wait for pods to be ready
echo "Waiting for pods to be ready..."
echo "Checking pod status..."
kubectl get pods -n $NAMESPACE

# Wait for pods with increased timeout and better error handling
if ! kubectl wait --namespace $NAMESPACE \
    --for=condition=ready pod \
    --selector=app=skypilot-api \
    --timeout=600s; then
    echo "Warning: Pod readiness check timed out. Checking pod status for debugging..."
    kubectl describe pods -n $NAMESPACE -l app=skypilot-api
    kubectl logs -n $NAMESPACE -l app=skypilot-api
    exit 1
fi

# Fix OAuth2 proxy configuration for kind cluster environment
echo "Fixing OAuth2 proxy ingress configuration for kind cluster..."
# The Helm chart sets auth-url to use $host which becomes "localhost:30082" from the client side,
# but the nginx ingress controller inside the cluster can't resolve "localhost" to reach the OAuth2 proxy.
# We need to patch the ingress to use the internal cluster service URL instead.
kubectl patch ingress skypilot-ingress -n $NAMESPACE --type='merge' -p='{
  "metadata": {
    "annotations": {
      "nginx.ingress.kubernetes.io/auth-url": "http://skypilot-oauth2-proxy.'$NAMESPACE'.svc.cluster.local:4180/oauth2/auth",
      "nginx.ingress.kubernetes.io/auth-signin": "http://localhost:'$NODEPORT'/oauth2/start?rd=$escaped_request_uri"
    }
  }
}'
echo "OAuth2 proxy ingress configuration updated ✓"

# Get the API server URL
echo "Getting API server URL..."
# For kind clusters, use localhost with NodePort since LoadBalancer doesn't get external IP
# The nginx ingress controller service from sky local up is exposed via NodePort
ENDPOINT=http://localhost:${NODEPORT}

echo "API server endpoint: $ENDPOINT"

# Test the API server with retry logic
echo "Testing API server endpoint..."
echo "Endpoint: $ENDPOINT"
MAX_RETRIES=10  # 5 minutes with 30 second intervals
RETRY_INTERVAL=30
RETRY_COUNT=0

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    echo "Attempt $((RETRY_COUNT + 1))/$MAX_RETRIES: Testing endpoint..."
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" ${ENDPOINT})
    echo "HTTP response code: $HTTP_CODE"

    # Check if response is not a 4xx error (nginx error page)
    if [ "$HTTP_CODE" -lt 400 ]; then
        echo "API server is responding successfully (HTTP $HTTP_CODE)!"
        break
    else
        echo "API server not ready yet (HTTP $HTTP_CODE - nginx error). Waiting ${RETRY_INTERVAL} seconds..."
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

echo "SkyPilot API server is ready and accessible!"
echo ""
echo "🎉 Setup complete! OAuth2 Proxy with Okta authentication is now configured."
echo ""
echo "To access the API server:"
echo "1. Open a browser and go to: $ENDPOINT"
echo "2. You'll be redirected to Okta for authentication"
echo "3. After successful login, you'll have access to the SkyPilot API"
echo ""
echo "For CLI access, use: sky api login -e $ENDPOINT"
echo ""



# Perform automated login
echo ""
echo "🔄 Performing automated login..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGIN_OUTPUT=$(python3 "$SCRIPT_DIR/okta_auto_login.py" "$ENDPOINT" "$OKTA_TEST_USERNAME" "$OKTA_TEST_PASSWORD" "$OKTA_CLIENT_ID")

if [[ $? -eq 0 ]] && [[ "$LOGIN_OUTPUT" == SUCCESS* ]]; then
    echo "✅ Automated test complete"
else
    echo "❌ Error happened during automated login test"
    echo "Login output: $LOGIN_OUTPUT"
    exit 1
fi
