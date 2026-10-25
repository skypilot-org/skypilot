#!/usr/bin/env bash
# Expose the vLLM model-server pod (launched by gpu_model_server.yaml on the GPU
# cluster) via a LoadBalancer Service, and print the reachable /v1 base URL to
# pass to run_split_cluster.py --head-url.
#
# A public LoadBalancer (the default here) is the simplest way for a sandbox
# cluster to reach a model server in a different cluster when the two can't
# share a private network. Front it with auth if it must be internet-facing.
#
# Usage: ./expose_model_server.sh <gpu-context> [pod-name-substr] [port]
set -euo pipefail
CTX="${1:?usage: expose_model_server.sh <gpu-context> [pod-name-substr] [port]}"
MATCH="${2:-vime-model-server}"
PORT="${3:-8000}"
NS=default
SVC=vime-model-server-lb

# Find the running model-server pod (SkyPilot names job/cluster pods after the
# cluster).
POD=$(kubectl --context "$CTX" -n "$NS" get pods --field-selector=status.phase=Running \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | grep -i "$MATCH" | head -1)
if [[ -z "$POD" ]]; then
  echo "No running pod matching '$MATCH' in $CTX/$NS" >&2; exit 1
fi
echo "model-server pod: $POD"

# Build a selector from a stable label SkyPilot sets on the pod.
SELKEY=$(kubectl --context "$CTX" -n "$NS" get pod "$POD" -o json \
  | python3 -c "import sys,json;l=json.load(sys.stdin)['metadata']['labels'];
k=[x for x in ('skypilot-cluster','skypilot-cluster-name','run') if x in l];
print(k[0] if k else '')")
if [[ -z "$SELKEY" ]]; then
  echo "Could not find a skypilot-cluster label on $POD; labels were:" >&2
  kubectl --context "$CTX" -n "$NS" get pod "$POD" --show-labels >&2; exit 1
fi
SELVAL=$(kubectl --context "$CTX" -n "$NS" get pod "$POD" -o jsonpath="{.metadata.labels.$SELKEY}")
echo "selector: $SELKEY=$SELVAL"

cat <<EOF | kubectl --context "$CTX" apply -f - >/dev/null
apiVersion: v1
kind: Service
metadata:
  name: $SVC
  namespace: $NS
spec:
  type: LoadBalancer
  selector:
    $SELKEY: "$SELVAL"
  ports:
  - name: http
    port: $PORT
    targetPort: $PORT
EOF

echo -n "waiting for external IP"
for _ in $(seq 1 60); do
  IP=$(kubectl --context "$CTX" -n "$NS" get svc "$SVC" -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
  [[ -n "$IP" ]] && break
  echo -n "."; sleep 5
done
echo
[[ -z "${IP:-}" ]] && { echo "LB IP not assigned yet" >&2; exit 1; }
echo "HEAD_URL=http://${IP}:${PORT}/v1"
