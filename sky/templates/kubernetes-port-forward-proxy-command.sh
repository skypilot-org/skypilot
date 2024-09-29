#!/usr/bin/env bash
set -uo pipefail

KUBE_CONTEXT=""
KUBE_NAMESPACE=""

# Parse flags
while getopts ":c:n:" opt; do
  case ${opt} in
    c)
      KUBE_CONTEXT="$OPTARG"
      ;;
    n)
      KUBE_NAMESPACE="$OPTARG"
      ;;
    \?)
      echo "Invalid option: -$OPTARG" >&2
      echo "Usage: $0 <pod_name> [-c kube_context] [-n kube_namespace]" >&2
      exit 1
      ;;
    :)
      echo "Option -$OPTARG requires an argument." >&2
      exit 1
      ;;
  esac
done

# Shift the processed options away so that $1 becomes the pod name
shift $((OPTIND -1))

# Check if pod name is passed as an argument
if [ $# -lt 1 ]; then
  echo "Usage: $0 <pod_name> [-c kube_context] [-n kube_namespace]" >&2
  exit 1
fi

POD_NAME="$1"  # The first positional argument is the name of the pod

# Checks if socat is installed
if ! command -v socat > /dev/null; then
  echo "Using 'port-forward' mode to run ssh session on Kubernetes instances requires 'socat' to be installed. Please install 'socat'" >&2
  exit
fi

# Checks if netcat is installed (may not be present in many docker images)
if ! command -v nc > /dev/null; then
  echo "Using 'port-forward' mode to run ssh session on Kubernetes instances requires 'nc' to be installed. Please install 'nc' (netcat)." >&2
  exit
fi

# Establishes connection between local port and the ssh jump pod using kube port-forward
# Instead of specifying a port, we let kubectl select a random port.
# This is preferred because of socket re-use issues in kubectl port-forward,
# see - https://github.com/kubernetes/kubernetes/issues/74551#issuecomment-769185879
KUBECTL_OUTPUT=$(mktemp)
KUBECTL_ARGS=()

if [ -n "$KUBE_CONTEXT" ]; then
  KUBECTL_ARGS+=("--context=$KUBE_CONTEXT")
fi
if [ -n "$KUBE_NAMESPACE" ]; then
  KUBECTL_ARGS+=("--namespace=$KUBE_NAMESPACE")
fi

kubectl "${KUBECTL_ARGS[@]}" port-forward pod/"${POD_NAME}" :22 > "${KUBECTL_OUTPUT}" 2>&1 &

# Capture the PID for the backgrounded kubectl command
K8S_PORT_FWD_PID=$!

# Wait until kubectl port-forward is ready
while ! grep -q "Forwarding from 127.0.0.1" "${KUBECTL_OUTPUT}"; do
    sleep 0.1
    # Handle the case where kubectl port-forward fails
    # It may fail if the kubeconfig is missing or invalid, or if the cluster is not reachable.
    if ! kill -0 "$K8S_PORT_FWD_PID" 2> /dev/null; then
        echo "kubectl port-forward failed. Error: $(cat "$KUBECTL_OUTPUT")" >&2
        rm -f "${KUBECTL_OUTPUT}"
        exit 1
    fi
done

# Extract the local port number assigned by kubectl.
local_port=$(awk -F: '/Forwarding from 127.0.0.1/{print $2}' "${KUBECTL_OUTPUT}" | awk -F' ' '{print $1}')

# Clean up the temporary file
rm -f "${KUBECTL_OUTPUT}"

# Add handler to terminate the port-forward process when this script exits.
trap "[[ -e /proc/$K8S_PORT_FWD_PID ]] && kill $K8S_PORT_FWD_PID" EXIT

# Checks if a connection to local_port of 127.0.0.1:[local_port] is established
while ! nc -z 127.0.0.1 "${local_port}"; do
    sleep 0.1
done

# Establishes two directional byte streams to handle stdin/stdout between
# terminal and the jump pod.
# socat process terminates when port-forward terminates.
socat - tcp:127.0.0.1:"${local_port}"
