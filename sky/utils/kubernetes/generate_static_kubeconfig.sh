#!/bin/bash
# This script creates a new k8s Service Account and generates a kubeconfig with
# its credentials. This Service Account has all the necessary permissions for
# SkyPilot. The kubeconfig is written in the current directory.
#
# You must configure your local kubectl to point to the right k8s cluster and
# have admin-level access.
#
# Note: all of the k8s resources are created in namespace "skypilot". If you
# delete any of these objects, SkyPilot will stop working.
#
# You can override the default namespace "skypilot" using the
# SKYPILOT_NAMESPACE environment variable.
# You can override the default service account name "skypilot-sa" using the
# SKYPILOT_SA_NAME environment variable.

set -eu -o pipefail

# Allow passing in common name and username in environment. If not provided,
# use default.
SKYPILOT_SA=${SKYPILOT_SA_NAME:-skypilot-sa}
NAMESPACE=${SKYPILOT_NAMESPACE:-default}

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

echo "Creating the Kubernetes Service Account with minimal RBAC permissions."
kubectl apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${NAMESPACE}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ${SKYPILOT_SA}
  namespace: ${NAMESPACE}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: skypilot-role
rules:
- apiGroups: ["*"]
  resources: ["*"]
  verbs: ["*"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: skypilot-crb
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: skypilot-role
subjects:
- kind: ServiceAccount
  name: ${SKYPILOT_SA}
  namespace: ${NAMESPACE}
EOF

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
EOF

SA_SECRET_NAME=${SKYPILOT_SA}
fi

# Note: service account token is stored base64-encoded in the secret but must
# be plaintext in kubeconfig.
SA_TOKEN=$(kubectl get -n ${NAMESPACE} secrets/${SA_SECRET_NAME} -o "jsonpath={.data['token']}" | base64 ${BASE64_DECODE_FLAG})
CA_CERT=$(kubectl get -n ${NAMESPACE} secrets/${SA_SECRET_NAME} -o "jsonpath={.data['ca\.crt']}")

# Extract cluster IP from the current context
CURRENT_CONTEXT=$(kubectl config current-context)
CURRENT_CLUSTER=$(kubectl config view -o jsonpath="{.contexts[?(@.name == \"${CURRENT_CONTEXT}\"})].context.cluster}")
CURRENT_CLUSTER_ADDR=$(kubectl config view -o jsonpath="{.clusters[?(@.name == \"${CURRENT_CLUSTER}\"})].cluster.server}")

echo "Writing kubeconfig."
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
  name: ${CURRENT_CONTEXT}
current-context: ${CURRENT_CONTEXT}
kind: Config
preferences: {}
users:
- name: ${CURRENT_CLUSTER}-${SKYPILOT_SA}
  user:
    token: ${SA_TOKEN}
EOF

echo "---
Done!

Copy the generated kubeconfig file to your SkyPilot Proxy server, and set the
kubeconfig_file parameter in your skypilot.yaml config file to point to this
kubeconfig file.

If you need access to multiple kubernetes clusters, you can generate additional
kubeconfig files using this script and then merge them using merge-kubeconfigs.sh.

Note: Kubernetes RBAC rules for SkyPilot were created, you won't need to create them manually."
