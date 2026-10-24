#!/usr/bin/env bash
# Create (or delete) a kind cluster for dashboard tests that need a real
# Kubernetes API: infra contexts, node and GPU inventory.
#
#   framework/kind_cluster.sh up   > kind.env   # prints E2E_KUBECONFIG/E2E_K8S_CONTEXT
#   framework/kind_cluster.sh down
#
# The node gets mock GPUs at the API level: an accelerator label and an
# nvidia.com/gpu capacity, which is what SkyPilot reads for GPU inventory.
# Nothing is scheduled on them.
#
# Inputs: E2E_KIND_NAME (default sky-e2e), E2E_KIND_GPUS (default 8),
#         E2E_KIND_ACCELERATOR (default h100), E2E_KIND_KUBECONFIG.
set -euo pipefail
NAME="${E2E_KIND_NAME:-sky-e2e}"
GPUS="${E2E_KIND_GPUS:-8}"
ACCEL="${E2E_KIND_ACCELERATOR:-h100}"
KUBECONFIG_OUT="${E2E_KIND_KUBECONFIG:-${TMPDIR:-/tmp}/kind-$NAME.kubeconfig}"
CONTEXT="kind-$NAME"
NODE="$NAME-control-plane"

if [[ "${1:-up}" == "down" ]]; then
  kind delete cluster --name "$NAME"
  rm -f "$KUBECONFIG_OUT"
  exit 0
fi

if ! kind get clusters 2>/dev/null | grep -qx "$NAME"; then
  kind create cluster --name "$NAME" --kubeconfig "$KUBECONFIG_OUT" --wait 120s >&2
else
  kind get kubeconfig --name "$NAME" > "$KUBECONFIG_OUT"
fi
export KUBECONFIG="$KUBECONFIG_OUT"
kubectl --context "$CONTEXT" label node "$NODE" --overwrite \
  "skypilot.co/accelerator=$ACCEL" >&2
# Node status is only writable through the API; kubectl proxy gives a plain
# HTTP endpoint for the JSON patch.
PROXY_PORT="${E2E_KIND_PROXY_PORT:-8011}"
kubectl --context "$CONTEXT" proxy --port="$PROXY_PORT" >/dev/null 2>&1 &
proxy_pid=$!
trap 'kill $proxy_pid 2>/dev/null || true' EXIT
for _ in $(seq 1 20); do
  curl -fsS "http://127.0.0.1:$PROXY_PORT/api" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fsS -X PATCH -H 'Content-Type: application/json-patch+json' \
  --data "[{\"op\":\"add\",\"path\":\"/status/capacity/nvidia.com~1gpu\",\"value\":\"$GPUS\"},{\"op\":\"add\",\"path\":\"/status/allocatable/nvidia.com~1gpu\",\"value\":\"$GPUS\"}]" \
  "http://127.0.0.1:$PROXY_PORT/api/v1/nodes/$NODE/status" >/dev/null
reported="$(kubectl --context "$CONTEXT" get node "$NODE" \
  -o jsonpath='{.status.allocatable.nvidia\.com/gpu}')"
[[ "$reported" == "$GPUS" ]] || { echo "GPU patch did not stick: '$reported'" >&2; exit 1; }
echo "# kind cluster $NAME ready with $GPUS mock $ACCEL GPUs" >&2
cat <<EOF
export E2E_KUBECONFIG=$KUBECONFIG_OUT
export E2E_K8S_CONTEXT=$CONTEXT
EOF
