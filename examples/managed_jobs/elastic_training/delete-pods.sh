#!/usr/bin/env bash
# Force-kills a random subset of workers-* pods to simulate node failures
# during elastic training. Usage: ./delete-pods.sh <number-of-pods-to-delete>
set -euo pipefail

NUM_PODS="${1:?Usage: $0 <number-of-pods-to-delete> [chunk-size] [parallelism]}"
CHUNK_SIZE="${2:-25}"
PARALLELISM="${3:-20}"
CONTEXT="nebius-mk8s-rohan-dev-e00pqvm6n59khq59zm"
NAMESPACE="default"

kubectl --context="$CONTEXT" -n "$NAMESPACE" get pods -o name \
  | grep '^pod/workers-' \
  | shuf -n "$NUM_PODS" \
  | xargs -n "$CHUNK_SIZE" -P "$PARALLELISM" kubectl --context="$CONTEXT" -n "$NAMESPACE" delete --force --grace-period=0
