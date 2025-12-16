#!/bin/bash
# This script creates a new k8s Service Account and generates a kubeconfig with
# its credentials. This Service Account has the minimal permissions necessary for
# SkyPilot. The kubeconfig is written in the current directory.
#
# Before running this script, you must configure your local kubectl to point to
# the right k8s cluster and have admin-level access.
#
# By default, this script will create a service account "sky-sa" in "default"
# namespace. If you want to use a different namespace or service account name:
#
#   * Specify SKYPILOT_NAMESPACE env var to override the default namespace where the service account is created.
#   * Specify SKYPILOT_SA_NAME env var to override the default service account name.
#   * Specify SKIP_SA_CREATION=1 to skip creating the service account and use an existing one
#   * Specify SUPER_USER=0 to create a service account with minimal permissions
#
# Usage:
#   # Create "sky-sa" service account in "default" namespace and generate kubeconfig
#   $ ./generate_kubeconfig.sh
#
#   # Create "my-sa" service account in "my-namespace" namespace and generate kubeconfig
#   $ SKYPILOT_SA_NAME=my-sa SKYPILOT_NAMESPACE=my-namespace ./generate_kubeconfig.sh
#
#   # Use an existing service account "my-sa" in "my-namespace" namespace and generate kubeconfig
#   $ SKIP_SA_CREATION=1 SKYPILOT_SA_NAME=my-sa SKYPILOT_NAMESPACE=my-namespace ./generate_kubeconfig.sh
#
#   # Create "sky-sa" service account with minimal permissions in "default" namespace (manual setup may be required)
#   $ SUPER_USER=0 ./generate_kubeconfig.sh

set -eu -o pipefail

# Allow passing in common name and username in environment. If not provided,
# use default.
SKYPILOT_SA=${SKYPILOT_SA_NAME:-sky-sa}
NAMESPACE=${SKYPILOT_NAMESPACE:-default}
SUPER_USER=${SUPER_USER:-1}

echo "=========================================="
echo "SkyPilot Kubeconfig Generation"
echo "=========================================="
echo "Service Account: ${SKYPILOT_SA}"
echo "Namespace:       ${NAMESPACE}"
if [ "${SUPER_USER}" != "1" ]; then
  echo "Permissions:     Minimal (manual setup may be required)"
  SUPER_USER=0
fi
echo ""

# Set OS specific values.
if [[ "$OSTYPE" == "linux-gnu" ]]; then
    BASE64_DECODE_FLAG="-d"
elif [[ "$OSTYPE" == "darwin"* ]]; then
    BASE64_DECODE_FLAG="-D"
elif [[ "$OSTYPE" == "linux-musl" ]]; then
    BASE64_DECODE_FLAG="-d"
else
    echo "Unknown OS ${OSTYPE}"
    exit 1
fi

# If the user has set SKIP_SA_CREATION=1, skip creating the service account.
if [ -z ${SKIP_SA_CREATION+x} ]; then
  echo "[1/3] Creating Kubernetes Service Account and RBAC permissions..."
  if [ "${SUPER_USER}" = "1" ]; then
    # Create service account with cluster-admin permissions
    kubectl apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${NAMESPACE}
  labels:
    parent: skypilot
---
kind: ServiceAccount
apiVersion: v1
metadata:
  name: ${SKYPILOT_SA}
  namespace: ${NAMESPACE}
  labels:
    parent: skypilot
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: ${SKYPILOT_SA}-cluster-admin
  labels:
    parent: skypilot
subjects:
  - kind: ServiceAccount
    name: ${SKYPILOT_SA}
    namespace: ${NAMESPACE}
roleRef:
  kind: ClusterRole
  name: cluster-admin
  apiGroup: rbac.authorization.k8s.io
EOF
  else
    # Original RBAC rules for minimal permissions
    kubectl apply -f - <<EOF
# Create/update namespace specified by the user
apiVersion: v1
kind: Namespace
metadata:
  name: ${NAMESPACE}
  labels:
    parent: skypilot
---
kind: ServiceAccount
apiVersion: v1
metadata:
  name: ${SKYPILOT_SA}
  namespace: ${NAMESPACE}
  labels:
    parent: skypilot
---
# Role for the service account
kind: Role
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: ${SKYPILOT_SA}-role
  namespace: ${NAMESPACE}
  labels:
    parent: skypilot
rules:
  - apiGroups: ["*"]  # Required for creating pods, services, secrets and other necessary resources in the namespace.
    resources: ["*"]
    verbs: ["*"]
---
# RoleBinding for the service account
kind: RoleBinding
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: ${SKYPILOT_SA}-rb
  namespace: ${NAMESPACE}
  labels:
    parent: skypilot
subjects:
  - kind: ServiceAccount
    name: ${SKYPILOT_SA}
roleRef:
  kind: Role
  name: ${SKYPILOT_SA}-role
  apiGroup: rbac.authorization.k8s.io
---
# ClusterRole for the service account
kind: ClusterRole
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: ${SKYPILOT_SA}-cluster-role
  namespace: ${NAMESPACE}
  labels:
    parent: skypilot
rules:
  - apiGroups: [""]
    resources: ["nodes"]  # Required for getting node resources.
    verbs: ["get", "list", "watch"]
  - apiGroups: ["node.k8s.io"]
    resources: ["runtimeclasses"]   # Required for autodetecting the runtime class of the nodes.
    verbs: ["get", "list", "watch"]
  - apiGroups: ["networking.k8s.io"]   # Required for exposing services through ingresses
    resources: ["ingressclasses"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]                 # Required for sky show-gpus command
    resources: ["pods"]
    verbs: ["get", "list"]
  - apiGroups: ["rbac.authorization.k8s.io"]   # Required for SkyPilot to inspect its own permissions
    resources: ["clusterroles", "clusterrolebindings", "roles", "rolebindings"]
    verbs: ["get", "list", "watch"]
---
# ClusterRoleBinding for the service account
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: ${SKYPILOT_SA}-cluster-role-binding
  namespace: ${NAMESPACE}
  labels:
    parent: skypilot
subjects:
  - kind: ServiceAccount
    name: ${SKYPILOT_SA}
    namespace: ${NAMESPACE}
roleRef:
  kind: ClusterRole
  name: ${SKYPILOT_SA}-cluster-role
  apiGroup: rbac.authorization.k8s.io
---
# Optional: If using object store mounting, create the skypilot-system namespace
apiVersion: v1
kind: Namespace
metadata:
  name: skypilot-system
  labels:
    parent: skypilot
---
# Optional: If using object store mounting, create role in the skypilot-system
# namespace to create FUSE device manager.
kind: Role
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: skypilot-system-service-account-role
  namespace: skypilot-system
  labels:
    parent: skypilot
rules:
  - apiGroups: ["*"]
    resources: ["*"]
    verbs: ["*"]
---
# Optional: If using object store mounting, create rolebinding in the skypilot-system
# namespace to create FUSE device manager.
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ${SKYPILOT_SA}-skypilot-system-role-binding-${NAMESPACE}
  namespace: skypilot-system  # Do not change this namespace
  labels:
    parent: skypilot
subjects:
  - kind: ServiceAccount
    name: ${SKYPILOT_SA}
    namespace: ${NAMESPACE}
roleRef:
  kind: Role
  name: skypilot-system-service-account-role
  apiGroup: rbac.authorization.k8s.io
EOF
  fi
# Apply optional ingress-related roles, but don't make the script fail if it fails
echo "      → Applying optional ingress permissions (skipped if ingress-nginx not installed)..."
kubectl apply -f - 2>/dev/null <<EOF || true
# Optional: Role for accessing ingress resources
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: ${SKYPILOT_SA}-role-ingress-nginx
  namespace: ingress-nginx  # Do not change this namespace
  labels:
    parent: skypilot
rules:
  - apiGroups: [""]
    resources: ["services"]
    verbs: ["list", "get", "watch"]
  - apiGroups: ["rbac.authorization.k8s.io"]
    resources: ["roles", "rolebindings"]
    verbs: ["list", "get", "watch"]
---
# Optional: RoleBinding for accessing ingress resources
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ${SKYPILOT_SA}-rolebinding-ingress-nginx
  namespace: ingress-nginx  # Do not change this namespace
  labels:
    parent: skypilot
subjects:
  - kind: ServiceAccount
    name: ${SKYPILOT_SA}
    namespace: ${NAMESPACE}
roleRef:
  kind: Role
  name: ${SKYPILOT_SA}-role-ingress-nginx  # Use the same name as the role at line 119
  apiGroup: rbac.authorization.k8s.io
EOF
else
  echo "[1/3] Skipping service account creation (using existing account)..."
fi

echo ""
echo "[2/3] Creating service account token..."

# Checks if secret entry was defined for Service account. If defined it means that Kubernetes server has a
# version bellow 1.24, otherwise one must manually create the secret and bind it to the Service account to have a non expiring token.
# After Kubernetes v1.24 Service accounts no longer generate automatic tokens/secrets.
# We can use kubectl create token but the token has a expiration time.
# https://github.com/kubernetes/kubernetes/blob/master/CHANGELOG/CHANGELOG-1.24.md#urgent-upgrade-notes
SA_SECRET_NAME=$(kubectl get -n ${NAMESPACE} sa/${SKYPILOT_SA} -o "jsonpath={.secrets[0]..name}")
if [ -z $SA_SECRET_NAME ]
then
# Create the secret and bind it to the desired SA
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
type: kubernetes.io/service-account-token
metadata:
  name: ${SKYPILOT_SA}
  namespace: ${NAMESPACE}
  annotations:
    kubernetes.io/service-account.name: "${SKYPILOT_SA}"
  labels:
    parent: skypilot
EOF

SA_SECRET_NAME=${SKYPILOT_SA}
fi

# Sleep for 2 seconds to allow the secret to be created before fetching it.
sleep 2

# Note: service account token is stored base64-encoded in the secret but must
# be plaintext in kubeconfig.
SA_TOKEN=$(kubectl get -n ${NAMESPACE} secrets/${SA_SECRET_NAME} -o "jsonpath={.data['token']}" | base64 ${BASE64_DECODE_FLAG})
CA_CERT=$(kubectl get -n ${NAMESPACE} secrets/${SA_SECRET_NAME} -o "jsonpath={.data['ca\.crt']}")

# Extract cluster IP from the current context
CURRENT_CONTEXT=$(kubectl config current-context)
CURRENT_CLUSTER=$(kubectl config view -o jsonpath="{.contexts[?(@.name == \"${CURRENT_CONTEXT}\"})].context.cluster}")
CURRENT_CLUSTER_ADDR=$(kubectl config view -o jsonpath="{.clusters[?(@.name == \"${CURRENT_CLUSTER}\"})].cluster.server}")

echo ""
echo "[3/3] Generating kubeconfig file..."

cat > kubeconfig <<EOF
apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: ${CA_CERT}
    server: ${CURRENT_CLUSTER_ADDR}
  name: ${CURRENT_CLUSTER}
contexts:
- context:
    cluster: ${CURRENT_CLUSTER}
    user: ${CURRENT_CLUSTER}-${SKYPILOT_SA}
    namespace: ${NAMESPACE}
  name: ${CURRENT_CONTEXT}
current-context: ${CURRENT_CONTEXT}
kind: Config
preferences: {}
users:
- name: ${CURRENT_CLUSTER}-${SKYPILOT_SA}
  user:
    token: ${SA_TOKEN}
EOF

echo ""
echo "=========================================="
echo "✓ SUCCESS!"
echo "=========================================="
echo ""
echo "Kubeconfig file created successfully!"
echo ""
echo "  Service Account: ${SKYPILOT_SA}"
echo "  Namespace:       ${NAMESPACE}"
echo "  Location:        $(pwd)/kubeconfig"
echo ""
echo "Next steps:"
echo "  Refer to this page for setting up the credential for remote API server:"
echo "  https://docs.skypilot.co/en/latest/reference/api-server/api-server-admin-deploy.html#optional-configure-cloud-accounts"
echo ""
