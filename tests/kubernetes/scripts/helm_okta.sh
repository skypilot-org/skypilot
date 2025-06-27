#!/bin/bash

# This script deploys SkyPilot with OAuth2 Proxy (Okta) authentication on a kind cluster
# created by 'sky local up'. It includes specific fixes for kind cluster networking issues.
#
# Key modifications for kind cluster compatibility:
# 1. Disables ingress-nginx subchart to avoid conflicts with the existing nginx from 'sky local up'
# 2. Patches OAuth2 proxy ingress annotations to use internal cluster service URLs
# 3. Uses localhost with NodePort for accessing the API server
#
# Prerequisites: Run 'sky local up' first to create the kind cluster with nginx ingress

NAMESPACE=skypilot
NODEPORT=30082
HTTPS_NODEPORT=30443


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

# Test the API server with retry logic30082
echo "Testing API server health endpoint..."
echo "Endpoint: $ENDPOINT/api/health"

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
