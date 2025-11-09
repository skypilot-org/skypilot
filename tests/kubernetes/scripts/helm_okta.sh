#!/bin/bash

# This script deploys SkyPilot with OAuth2 Proxy (Okta) authentication on a kind cluster
# created by 'sky local up'. It includes specific fixes for kind cluster networking issues.
#
# Key modifications for kind cluster compatibility:
# 1. Disables ingress-nginx subchart to avoid conflicts with the existing nginx from 'sky local up'
# 2. Disables nginx admission webhook to allow OAuth2 proxy snippet annotations
# 3. Patches OAuth2 proxy ingress annotations to use internal cluster service URLs
# 4. Uses localhost with NodePort for accessing the API server
# 5. Supports automated login for testing (when test credentials are provided)
#
# Prerequisites:
# - Run 'sky local up' first to create the kind cluster with nginx ingress
# - Set required environment variables: OKTA_CLIENT_ID, OKTA_CLIENT_SECRET, OKTA_TEST_USERNAME, OKTA_TEST_PASSWORD, OKTA_ISSUER_URL
# - Configure your Okta app with both redirect URIs:
#   * http://localhost:30082/oauth2/callback (for host access)
#   * http://host.docker.internal:30082/oauth2/callback (for container access)
#
# Usage:
#   OKTA_CLIENT_ID=your_client_id OKTA_CLIENT_SECRET=your_secret \
#   OKTA_TEST_USERNAME=test@example.com OKTA_TEST_PASSWORD=pass \
#   OKTA_ISSUER_URL=https://your-org.okta.com \
#   DOCKER_IMAGE=skypilot:local \
#   ./helm_okta.sh
#
#   All environment variables are required:
#   - OKTA_CLIENT_ID: OAuth application client ID
#   - OKTA_CLIENT_SECRET: OAuth application client secret
#   - OKTA_TEST_USERNAME: Test user email/username
#   - OKTA_TEST_PASSWORD: Test user password
#   - OKTA_ISSUER_URL: Okta issuer URL (e.g., https://your-org.okta.com)
#   - DOCKER_IMAGE: Local docker image name and tag to build

NAMESPACE=skypilot
NODEPORT=30082
HTTPS_NODEPORT=30099
RELEASE_NAME=skypilot

# Cleanup function to delete namespace and resources
cleanup() {
    echo ""
    echo "🧹 Cleaning up resources..."
    if kubectl get namespace $NAMESPACE >/dev/null 2>&1; then
        echo "Deleting namespace $NAMESPACE..."
        kubectl delete namespace $NAMESPACE --ignore-not-found=true
        # Wait until namespace actually terminated.
        while kubectl get namespace $NAMESPACE >/dev/null 2>&1; do
            echo "Waiting for namespace $NAMESPACE to be deleted..."
            sleep 1
        done
        echo "✅ Namespace $NAMESPACE deleted"
    else
        echo "Namespace $NAMESPACE does not exist, skipping deletion"
    fi

    # Restore sky/server/common.py to original state
    echo "Restoring sky/server/common.py..."
    if git checkout sky/server/common.py 2>/dev/null; then
        echo "✅ sky/server/common.py restored"
    else
        echo "⚠️  Could not restore sky/server/common.py (file may not be tracked or changes may not exist)"
    fi

    # Remove api_server section from ~/.sky/config.yaml if it exists
    local config_file="$HOME/.sky/config.yaml"
    if [ -f "$config_file" ] && grep -q "^api_server:" "$config_file"; then
        echo "Removing api_server section from $config_file..."
        sed -i '/^api_server:/,/^[[:space:]]*endpoint:/d' "$config_file"
        echo "✅ api_server section removed from config.yaml"
    else
        echo "⚠️  $config_file does not exist or does not contain api_server section"
    fi

    # Remove ~/.sky/cookies.txt if it exists
    local cookies_file="$HOME/.sky/cookies.txt"
    if [ -f "$cookies_file" ]; then
        echo "Removing $cookies_file..."
        rm -f "$cookies_file"
        echo "✅ cookies.txt removed"
    else
        echo "⚠️  $cookies_file does not exist"
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

if [[ -z "$OKTA_ISSUER_URL" ]]; then
    echo "❌ OKTA_ISSUER_URL is required"
    exit 1
fi

# Set default DOCKER_IMAGE if not provided
if [[ -z "$DOCKER_IMAGE" ]]; then
    DOCKER_IMAGE="skypilot:local"
    echo "ℹ️  DOCKER_IMAGE not provided, using default: $DOCKER_IMAGE"
fi

echo "Using OAuth client ID: $OKTA_CLIENT_ID"
echo "Using test credentials for user: $OKTA_TEST_USERNAME"
echo "Using Okta issuer URL: $OKTA_ISSUER_URL"
echo "Building Docker image locally: $DOCKER_IMAGE"

# Debug: Check if running inside Docker container
if [ -f /.dockerenv ]; then
    echo "ℹ️  Running inside Docker container"
fi

# Determine the appropriate hostname for accessing the cluster
if [ -f /.dockerenv ]; then
    # Running inside Docker container, use host.docker.internal to access host
    CLUSTER_HOST="host.docker.internal"
    echo "ℹ️  Using host.docker.internal to access kind cluster from container"
else
    # Running on host, use localhost
    CLUSTER_HOST="localhost"
    echo "ℹ️  Using localhost to access kind cluster from host"
fi

# Verify that nginx ingress controller is already running (installed by sky local up)
echo "Verifying nginx ingress controller is running..."
if ! kubectl get pods -n ingress-nginx -l app.kubernetes.io/component=controller --no-headers | grep -q "Running"; then
    echo "ERROR: nginx ingress controller is not running. Please run 'sky local up' first."
    exit 1
fi
echo "nginx ingress controller is running ✓"

# Disable nginx ingress admission webhook to allow snippet annotations
echo "Disabling nginx ingress admission webhook to allow OAuth2 proxy snippet annotations..."
if kubectl get validatingwebhookconfigurations ingress-nginx-admission >/dev/null 2>&1; then
    kubectl delete validatingwebhookconfigurations ingress-nginx-admission
    echo "✅ nginx ingress admission webhook disabled"
else
    echo "nginx ingress admission webhook not found, skipping deletion"
fi

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
if [ $? -ne 0 ]; then
    echo "Error: Failed to patch nginx ingress controller service"
    exit 1
fi
echo "nginx ingress controller configured for NodePort $NODEPORT ✓"

# Build the Docker image locally
echo "Building Docker image locally..."
echo "docker buildx build -t $DOCKER_IMAGE $BUILD_ARGS --load -f Dockerfile ."
docker buildx build -t $DOCKER_IMAGE $BUILD_ARGS --load -f Dockerfile .
if [ $? -ne 0 ]; then
    echo "❌ Failed to build Docker image"
    exit 1
fi
echo "✅ Docker image built successfully"

# Remove old images from kind cluster to avoid tag conflicts
echo "Removing old $DOCKER_IMAGE images from kind cluster..."
docker exec skypilot-control-plane crictl images | grep "$(echo $DOCKER_IMAGE | cut -d: -f1)" | awk '{print $3}' | while read img_id; do
    echo "Removing old image: $img_id"
    docker exec skypilot-control-plane crictl rmi "$img_id" 2>/dev/null || true
done
echo "✅ Old images removed from kind cluster"

# Load the image into kind cluster
echo "Loading image $DOCKER_IMAGE into kind cluster..."
kind load docker-image $DOCKER_IMAGE --name skypilot
echo "✅ Docker image loaded into kind cluster"

# Get the image ID of the image we just built
LATEST_IMAGE_ID=$(docker images --format "{{.ID}}" $DOCKER_IMAGE | head -1)
echo "Latest built image ID: $LATEST_IMAGE_ID"

# Verify the specific image ID is loaded in the cluster
echo "Verifying latest image ID is loaded in Kind cluster..."
if docker exec skypilot-control-plane crictl images | grep -q "$LATEST_IMAGE_ID"; then
    echo "✅ Latest image ID $LATEST_IMAGE_ID confirmed in Kind cluster"
else
    echo "❌ Latest image ID $LATEST_IMAGE_ID not found in Kind cluster"
    echo "Available skypilot images in cluster:"
    docker exec skypilot-control-plane crictl images | grep skypilot
    exit 1
fi

# Add required Helm repositories for dependencies
echo "Adding required Helm repositories..."
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update
echo "✅ Helm repositories added successfully"

# Build Helm chart dependencies with automatic update fallback
echo "Building Helm chart dependencies..."
if ! helm dependency build ./charts/skypilot; then
    echo "Lock file is likely out of sync. Running 'helm dependency update'..."
    if ! helm dependency update ./charts/skypilot; then
        echo "❌ Failed to update Helm chart dependencies"
        exit 1
    fi
    echo "Retrying 'helm dependency build'..."
    if ! helm dependency build ./charts/skypilot; then
        echo "❌ Failed to build Helm chart dependencies after update"
        exit 1
    fi
fi
echo "✅ Helm chart dependencies built successfully"

# Debug: List available images in kind cluster
echo "Debug: Listing available images in kind cluster..."
docker exec skypilot-control-plane crictl images | grep -E "(skypilot|local)" || echo "No skypilot images found"

deploy_and_login() {
    local mode="$1"
    echo "============================================="
    echo "Deploying SkyPilot with OAuth mode: $mode"
    echo "============================================="

    # Check if namespace is actively being terminated, if so wait.
    status=$(kubectl get namespace "$NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null || true)

    if [[ "$status" == "Terminating" ]]; then
      echo "⏳ Namespace '$NAMESPACE' is terminating. Waiting for it to be deleted..."
      # Poll until it's gone
      while kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; do
        sleep 2
      done
      echo "✅ Namespace '$NAMESPACE' has been fully deleted."
    elif [[ "$status" == "Active" ]]; then
      echo "✅ Namespace '$NAMESPACE' exists and is active — nothing to wait for."
    else
      echo "✅ Namespace '$NAMESPACE' does not exist."
    fi

    echo "✅ Namespace $NAMESPACE deleted"

    echo "Installing Skypilot Helm chart..."
    if [[ "$mode" == "legacy" ]]; then
        helm upgrade --install $RELEASE_NAME ./charts/skypilot --devel \
            --namespace $NAMESPACE \
            --create-namespace \
            --set apiService.image=$DOCKER_IMAGE \
            --set imagePullPolicy=Never \
            --set apiService.resources.requests.cpu=2 \
            --set apiService.resources.requests.memory=4Gi \
            --set apiService.resources.limits.cpu=2 \
            --set apiService.resources.limits.memory=4Gi \
            --set apiService.skipResourceCheck=true \
            --set auth.oauth.enabled=false \
            --set ingress.enabled=true \
            --set ingress.oauth2-proxy.enabled=true \
            --set ingress.oauth2-proxy.oidc-issuer-url="$OKTA_ISSUER_URL" \
            --set ingress.oauth2-proxy.client-id="$OKTA_CLIENT_ID" \
            --set ingress.oauth2-proxy.client-secret="$OKTA_CLIENT_SECRET" \
            --set ingress-nginx.enabled=false
    else
        helm upgrade --install $RELEASE_NAME ./charts/skypilot --devel \
            --namespace $NAMESPACE \
            --create-namespace \
            --set apiService.image=$DOCKER_IMAGE \
            --set imagePullPolicy=Never \
            --set apiService.resources.requests.cpu=2 \
            --set apiService.resources.requests.memory=4Gi \
            --set apiService.resources.limits.cpu=2 \
            --set apiService.resources.limits.memory=4Gi \
            --set apiService.skipResourceCheck=true \
            --set auth.oauth.enabled=true \
            --set auth.oauth.oidc-issuer-url="$OKTA_ISSUER_URL" \
            --set auth.oauth.client-id="$OKTA_CLIENT_ID" \
            --set auth.oauth.client-secret="$OKTA_CLIENT_SECRET" \
            --set ingress-nginx.enabled=false
    fi

    if [ $? -ne 0 ]; then
        echo "❌ Failed to install Helm chart"
        exit 1
    fi
    echo "✅ Helm chart installed successfully"

    # Fix imagePullPolicy for Docker-in-Docker environments
    # The Helm chart hardcodes imagePullPolicy: Always, but we need Never for local images
    # Patch BOTH the main container and the logrotate sidecar
    echo "Fixing imagePullPolicy for local Docker image..."
    kubectl patch deployment skypilot-api-server -n $NAMESPACE -p '{"spec":{"template":{"spec":{"containers":[{"name":"skypilot-api","imagePullPolicy":"Never"},{"name":"logrotate","imagePullPolicy":"Never"}]}}}}'
    echo "✅ imagePullPolicy patched to Never for all containers"

    # Wait for deployment rollout to complete after patching
    # This ensures the new pod is created and the PVC is bound
    echo "Waiting for deployment rollout to complete..."
    if ! kubectl rollout status deployment/skypilot-api-server -n $NAMESPACE --timeout=600s; then
        echo "Warning: Deployment rollout check failed. Checking deployment status..."
        kubectl describe deployment skypilot-api-server -n $NAMESPACE
        exit 1
    fi
    echo "✅ Deployment rollout completed"

    # Wait for pods to be ready
    echo "Waiting for pods to be ready..."

    # Wait for pods with increased timeout and better error handling
    if ! kubectl wait --namespace $NAMESPACE \
        --for=condition=ready pod \
        --selector=app=skypilot-api \
        --timeout=600s; then
        echo "Warning: Pod readiness check timed out. Checking pod status and cluster resources for debugging..."

        echo "=== Pod Status ==="
        kubectl describe pods -n $NAMESPACE -l app=skypilot-api

        echo "=== Pod Logs ==="
        kubectl logs -n $NAMESPACE -l app=skypilot-api

        echo "=== Cluster Node Resources ==="
        kubectl describe nodes

        echo "=== Events ==="
        kubectl get events -n $NAMESPACE --sort-by=.metadata.creationTimestamp

        echo "=== Resource Usage ==="
        kubectl top nodes 2>/dev/null || echo "Metrics server not available"

        exit 1
    fi

    echo "Patching ingress configuration for kind cluster..."
    # Base patch for skypilot-ingress rules applicable to both modes
    kubectl patch ingress skypilot-ingress -n $NAMESPACE --type='merge' -p='{
      "spec": {
        "rules": [
          {
            "host": "localhost",
            "http": {
              "paths": [
                {
                  "path": "/",
                  "pathType": "Prefix",
                  "backend": {
                    "service": {
                      "name": "skypilot-api-service",
                      "port": { "number": 80 }
                    }
                  }
                }
              ]
            }
          },
          {
            "host": "host.docker.internal",
            "http": {
              "paths": [
                {
                  "path": "/",
                  "pathType": "Prefix",
                  "backend": {
                    "service": {
                      "name": "skypilot-api-service",
                      "port": { "number": 80 }
                    }
                  }
                }
              ]
            }
          }
        ]
      }
    }'

    if [[ "$mode" == "legacy" ]]; then
        # Add oauth2-proxy annotations to main ingress
        kubectl patch ingress skypilot-ingress -n $NAMESPACE --type='merge' -p='{
          "metadata": {
            "annotations": {
              "nginx.ingress.kubernetes.io/auth-url": "http://skypilot-oauth2-proxy.'$NAMESPACE'.svc.cluster.local:4180/oauth2/auth",
              "nginx.ingress.kubernetes.io/auth-signin": "http://$host:'$NODEPORT'/oauth2/start?rd=$escaped_request_uri",
              "nginx.ingress.kubernetes.io/auth-snippet": null,
              "nginx.ingress.kubernetes.io/configuration-snippet": null
            }
          }
        }'

        # Patch oauth2-proxy ingress hosts if it exists
        if kubectl get ingress skypilot-oauth2-proxy -n $NAMESPACE >/dev/null 2>&1; then
            kubectl patch ingress skypilot-oauth2-proxy -n $NAMESPACE --type='merge' -p='{
              "spec": {
                "rules": [
                  {"host": "localhost", "http": {"paths": [{"path": "/oauth2", "pathType": "Prefix", "backend": {"service": {"name": "skypilot-oauth2-proxy", "port": {"number": 4180}}}}]}},
                  {"host": "host.docker.internal", "http": {"paths": [{"path": "/oauth2", "pathType": "Prefix", "backend": {"service": {"name": "skypilot-oauth2-proxy", "port": {"number": 4180}}}}]}}
                ]
              }
            }'
        fi
    else
        # Ensure oauth annotations are removed for new auth mode
        kubectl patch ingress skypilot-ingress -n $NAMESPACE --type='merge' -p='{
          "metadata": {
            "annotations": {
              "nginx.ingress.kubernetes.io/auth-url": null,
              "nginx.ingress.kubernetes.io/auth-signin": null,
              "nginx.ingress.kubernetes.io/auth-snippet": null,
              "nginx.ingress.kubernetes.io/configuration-snippet": null
            }
          }
        }'
    fi
    echo "Ingress configuration updated ✓"

    # Get the API server URL
    echo "Getting API server URL..."
    ENDPOINT=http://${CLUSTER_HOST}:${NODEPORT}
    echo "API server endpoint: $ENDPOINT"

    # Test the API server with retry logic
    echo "Testing API server endpoint ($mode)..."
    MAX_RETRIES=10
    RETRY_INTERVAL=30
    RETRY_COUNT=0
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        echo "Attempt $((RETRY_COUNT + 1))/$MAX_RETRIES: Testing endpoint..."
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" ${ENDPOINT})
        echo "HTTP response code: $HTTP_CODE"
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

    echo "🔄 Performing automated login for mode: $mode..."
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    LOGIN_OUTPUT=$(python3 "$SCRIPT_DIR/okta_auto_login.py" direct --endpoint "$ENDPOINT" --username "$OKTA_TEST_USERNAME" --password "$OKTA_TEST_PASSWORD" 2>&1)
    LOGIN_EXIT_CODE=$?
    echo "Login output: $LOGIN_OUTPUT"
    if [[ $LOGIN_EXIT_CODE -eq 0 ]] && echo "$LOGIN_OUTPUT" | grep -q "SUCCESS:"; then
        echo "✅ Automated test complete for mode: $mode"
    else
        echo "❌ Error happened during automated login test for mode: $mode"
        echo "Login exit code: $LOGIN_EXIT_CODE"
        exit 1
    fi
}

# Run tests for both legacy ingress.oauth2-proxy route and new auth.oauth route
deploy_and_login "legacy"

# Clean up SkyPilot resources between tests
echo ""
echo "🧹 Cleaning up SkyPilot resources between tests..."
kubectl delete namespace $NAMESPACE --ignore-not-found=true
echo "✅ Namespace $NAMESPACE deleted"

deploy_and_login "new"

# Apply cookie header fix to sky/server/common.py before sky api login
echo "Applying cookie header fix to sky/server/common.py..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Find the project root by looking for sky/server/common.py
COMMON_PY="$(cd "$SCRIPT_DIR/../../.." && pwd)/sky/server/common.py"
if [ -f "$COMMON_PY" ]; then
    # Use sed to replace the simple cookie assignment with the multi-line version
    # This fixes the requests library cookie issue with localhost:non-standard-port
    sed -i '/kwargs\['\''cookies'\''\] = get_api_cookie_jar()/c\
        cookie_jar = get_api_cookie_jar()\
        if cookie_jar:\
            # Convert cookie jar to Cookie header string to work around requests\
            # library edge case: cookies are not sent when using localhost with\
            # non-standard ports (e.g., localhost:30082), even when domain matches.\
            # The requests library'\''s cookie filtering logic incorrectly filters out\
            # valid cookies in this case. Setting the Cookie header manually\
            # bypasses this filtering and guarantees cookies are sent.\
            cookie_parts = []\
            for cookie in cookie_jar:\
                cookie_parts.append(f'\''{cookie.name}={cookie.value}'\'')\
            if cookie_parts:\
                if '\''Cookie'\'' not in headers:\
                    headers['\''Cookie'\''] = '\''; '\''.join(cookie_parts)\
        kwargs['\''cookies'\''] = cookie_jar' "$COMMON_PY"
    echo "✅ Cookie header fix applied"
else
    echo "⚠️  sky/server/common.py not found at $COMMON_PY"
    exit 1
fi

# sky api login
python3 "$SCRIPT_DIR/okta_auto_login.py" sky-api --endpoint "$ENDPOINT" --username "$OKTA_TEST_USERNAME" --password "$OKTA_TEST_PASSWORD" || (echo "❌ Failed: sky api login" && exit 1)

# run basic k8s ssh test
pytest tests/smoke_tests/test_basic.py::test_kubernetes_ssh_proxy_connection --kubernetes || (echo "❌ Failed: basic k8s ssh test" && exit 1)
