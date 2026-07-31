#!/bin/bash
# SkyPilot rsync remote shell for Kubernetes pods.
#
# This local patch avoids a kubectl exec -i EOF deadlock seen with SSH node
# pools by forwarding pod port 22 and running rsync over real SSH.

set -u

if ! command -v nc >/dev/null 2>&1; then
    echo "Error: netcat (nc) is required but not installed." >&2
    exit 1
fi
url_decode() {
    printf '%s\n' "$1" | sed 's|%40|@|g' | sed 's|%3A|:|g' | sed 's|%2B|+|g' | sed 's|%2F|/|g'
}

if [ "$1" = "-l" ]; then
    shift
    pod=$1
    shift
    encoded_namespace_context=$1
    shift
    echo "pod: $pod" >&2
    namespace_context=$(url_decode "$encoded_namespace_context")
    echo "namespace_context: $namespace_context" >&2
else
    encoded_pod_namespace_context=$1
    shift
    pod_namespace_context=$(url_decode "$encoded_pod_namespace_context")
    echo "pod_namespace_context: $pod_namespace_context" >&2
    pod=$(echo "$pod_namespace_context" | cut -d@ -f1)
    echo "pod: $pod" >&2
    namespace_context=$(echo "$pod_namespace_context" | cut -d@ -f2-)
    echo "namespace_context: $namespace_context" >&2
fi

namespace=$(echo "$namespace_context" | cut -d+ -f1)
echo "namespace: $namespace" >&2
context=$(echo "$namespace_context" | grep '+' >/dev/null && echo "$namespace_context" | cut -d+ -f2- || echo "")
echo "context: $context" >&2
context_lower=$(echo "$context" | tr '[:upper:]' '[:lower:]')
container="${SKYPILOT_K8S_EXEC_CONTAINER:-ray-node}"
echo "container: $container" >&2

if [[ "$pod" == *"/"* ]]; then
    echo "Resource contains type: $pod" >&2
    resource_type=$(echo "$pod" | cut -d/ -f1)
    resource_name=$(echo "$pod" | cut -d/ -f2)
    echo "Resource type: $resource_type, Resource name: $resource_name" >&2
else
    resource_type="pod"
    resource_name=$pod
    echo "Assuming resource is a pod: $resource_name" >&2
fi

sky_key="${SKYPILOT_SSH_KEY:-}"
if [ -z "$sky_key" ]; then
    for candidate in "${HOME:-}"/.sky/clients/*/ssh/sky-key; do
        if [ -f "$candidate" ]; then
            sky_key="$candidate"
            break
        fi
    done
fi
if [ -z "$sky_key" ] || [ ! -f "$sky_key" ]; then
    echo "Could not find SkyPilot SSH key. Set SKYPILOT_SSH_KEY to the pod SSH private key." >&2
    exit 1
fi
chmod 600 "$sky_key" 2>/dev/null || true
echo "ssh_key: $sky_key" >&2

pick_port() {
    base=$((20000 + ($$ % 20000)))
    i=0
    while [ "$i" -lt 200 ]; do
        port=$((base + i))
        if ! nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then
            echo "$port"
            return 0
        fi
        i=$((i + 1))
    done
    return 1
}

local_port=$(pick_port) || {
    echo "Could not find a free local port for kubectl port-forward." >&2
    exit 1
}

pf_pid=""
pf_log="${TMPDIR:-/tmp}/skypilot-rsync-port-forward-${resource_name}-${local_port}.log"

cleanup() {
    if [ -n "$pf_pid" ]; then
        kill "$pf_pid" >/dev/null 2>&1 || true
        wait "$pf_pid" >/dev/null 2>&1 || true
    fi
    rm -f "$pf_log" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if [ -z "$context" ] || [ "$context_lower" = "none" ]; then
    kubectl port-forward "$resource_type/$resource_name" -n "$namespace" --kubeconfig=/dev/null --address 127.0.0.1 "${local_port}:22" >"$pf_log" 2>&1 &
else
    kubectl port-forward "$resource_type/$resource_name" -n "$namespace" --context="$context" --address 127.0.0.1 "${local_port}:22" >"$pf_log" 2>&1 &
fi
pf_pid=$!

count=0
max_count=600
until nc -z 127.0.0.1 "$local_port" >/dev/null 2>&1; do
    if ! kill -0 "$pf_pid" >/dev/null 2>&1; then
        echo "kubectl port-forward exited before port ${local_port} became ready." >&2
        cat "$pf_log" >&2 2>/dev/null || true
        exit 1
    fi
    if [ "$count" -ge "$max_count" ]; then
        echo "Timed out waiting for kubectl port-forward to pod SSH port 22." >&2
        cat "$pf_log" >&2 2>/dev/null || true
        exit 1
    fi
    sleep 0.5
    count=$((count + 1))
done

ssh_opts=(
    -p "$local_port"
    -i "$sky_key"
    -o StrictHostKeyChecking=no
    -o UserKnownHostsFile=/dev/null
    -o IdentitiesOnly=yes
    -o LogLevel=ERROR
    -o ConnectTimeout=5
)

MAX_WAIT_TIME_SECONDS=300
MAX_WAIT_COUNT=$((MAX_WAIT_TIME_SECONDS * 2))

exec ssh "${ssh_opts[@]}" sky@127.0.0.1 \
    bash --norc --noprofile -c \
    'count=0; until command -v rsync >/dev/null 2>&1; do if [ "$count" -ge '"$MAX_WAIT_COUNT"' ]; then echo "Error when trying to rsync files to kubernetes cluster. Package installation may have failed." >&2; exit 1; fi; sleep 0.5; count=$((count+1)); done; exec "$@"' \
    -- "$@"
