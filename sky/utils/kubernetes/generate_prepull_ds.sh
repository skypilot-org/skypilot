#!/usr/bin/env bash
# Generate a DaemonSet manifest that pre-pulls (warms) container images onto
# every node of a Kubernetes cluster, so pods that use those images start fast
# instead of waiting on a cold image pull.
#
# Each image is listed as an init container: Kubernetes must pull an init
# container's image before it can run, so the pull is the whole point -- the
# container itself does nothing and exits immediately. A tiny `pause` container
# keeps the DaemonSet pod scheduled on the node.
#
# Usage:
#   ./generate_prepull_ds.sh IMAGE [IMAGE ...]
#
# Options (environment variables):
#   NAMESPACE            Namespace to deploy into        (default: default)
#   NODE_SELECTOR_KEY    Restrict to nodes with this label key   (default: all nodes)
#   NODE_SELECTOR_VALUE  Value for NODE_SELECTOR_KEY
#   PULL_SECRET          dockerconfigjson secret name for private registries
#
# Examples:
#   # Print the manifest
#   ./generate_prepull_ds.sh nvcr.io/nvidia/pytorch:24.05-py3 my-registry/model:v1
#
#   # Apply it directly
#   ./generate_prepull_ds.sh nvcr.io/nvidia/pytorch:24.05-py3 | kubectl apply -f -
#
#   # GPU nodes only, private registry
#   NODE_SELECTOR_KEY=nvidia.com/gpu.present NODE_SELECTOR_VALUE=true \
#     PULL_SECRET=regcred ./generate_prepull_ds.sh my-registry/model:v1 | kubectl apply -f -

set -euo pipefail

NAMESPACE="${NAMESPACE:-default}"
NODE_SELECTOR_KEY="${NODE_SELECTOR_KEY:-}"
NODE_SELECTOR_VALUE="${NODE_SELECTOR_VALUE:-}"
PULL_SECRET="${PULL_SECRET:-}"

if [[ "$#" -eq 0 ]]; then
  echo "Error: provide at least one image to pre-pull." >&2
  echo "Usage: $0 IMAGE [IMAGE ...]" >&2
  exit 1
fi

for img in "$@"; do
  if [[ -z "${img}" ]]; then
    echo "Error: empty image argument." >&2
    exit 1
  fi
done

# NODE_SELECTOR_KEY and NODE_SELECTOR_VALUE must be set together, else the
# generated nodeSelector is either half-formed (matches ~no nodes) or silently
# dropped -- both fail without any error at apply time.
if [[ -n "${NODE_SELECTOR_KEY}" && -z "${NODE_SELECTOR_VALUE}" ]] ||
   [[ -z "${NODE_SELECTOR_KEY}" && -n "${NODE_SELECTOR_VALUE}" ]]; then
  echo "Error: NODE_SELECTOR_KEY and NODE_SELECTOR_VALUE must both be set or both be empty." >&2
  exit 1
fi

# NAMESPACE and PULL_SECRET are interpolated into the manifest; validate them as
# Kubernetes names so a stray character can't produce broken or injected YAML.
_valid_k8s_name() { [[ "$1" =~ ^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$ ]]; }
if ! _valid_k8s_name "${NAMESPACE}"; then
  echo "Error: invalid NAMESPACE '${NAMESPACE}' (must be a lowercase DNS name)." >&2
  exit 1
fi
if [[ -n "${PULL_SECRET}" ]] && ! _valid_k8s_name "${PULL_SECRET}"; then
  echo "Error: invalid PULL_SECRET '${PULL_SECRET}' (must be a lowercase DNS name)." >&2
  exit 1
fi

emit_init_containers() {
  local i=0 img
  for img in "$@"; do
    cat <<EOF
        - name: prepull-${i}
          image: "${img}"
          imagePullPolicy: IfNotPresent
          command: ["sh", "-c", "exit 0"]
          resources:
            requests: { cpu: "10m", memory: "16Mi" }
            limits:   { cpu: "50m", memory: "64Mi" }
EOF
    i=$((i + 1))
  done
}

# Assemble the optional spec-level blocks into a string (not via $(...), which
# would strip the trailing newline) so nothing -- not even a blank line -- is
# emitted when a block is unset.
extra=""
if [[ -n "${NODE_SELECTOR_KEY}" ]]; then
  extra+="      nodeSelector:
        ${NODE_SELECTOR_KEY}: \"${NODE_SELECTOR_VALUE}\"
"
fi
if [[ -n "${PULL_SECRET}" ]]; then
  extra+="      imagePullSecrets:
        - name: \"${PULL_SECRET}\"
"
fi

cat <<EOF
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: skypilot-image-prepuller
  namespace: "${NAMESPACE}"
  labels:
    app.kubernetes.io/name: skypilot-image-prepuller
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: skypilot-image-prepuller
  template:
    metadata:
      labels:
        app.kubernetes.io/name: skypilot-image-prepuller
    spec:
${extra}      tolerations:
        - operator: "Exists"   # tolerate all taints (e.g. GPU nodes)...
      affinity:
        nodeAffinity:          # ...but keep off control-plane nodes
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
              - matchExpressions:
                  - key: node-role.kubernetes.io/control-plane
                    operator: DoesNotExist
      initContainers:
$(emit_init_containers "$@")
      containers:
        - name: pause
          image: registry.k8s.io/pause:3.9
          resources:
            requests: { cpu: "10m", memory: "16Mi" }
            limits:   { cpu: "50m", memory: "64Mi" }
EOF
