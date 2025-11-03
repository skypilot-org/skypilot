#!/bin/bash
set -e

# Usage: delete_cluster.sh <provider> [args...]
# Providers:
#  - gcp: args => <CLUSTER_NAME> <PROJECT_ID> <ZONE>
#  - aws: args => <CLUSTER_NAME> <REGION>

PROVIDER=${1:-"gcp"}
shift || true

# Global defaults
CLUSTER_NAME_DEFAULT="skypilot-helm-test-cluster"

case "$PROVIDER" in
  gcp|GCP)
    CLUSTER_NAME=${1:-"$CLUSTER_NAME_DEFAULT"}
    PROJECT_ID=${2:-$(gcloud config get-value project)}
    ZONE=${3:-"us-central1-a"}
    echo "Deleting GKE cluster '$CLUSTER_NAME' in project '$PROJECT_ID', zone '$ZONE'..."
    gcloud container clusters delete "$CLUSTER_NAME" \
        --project="$PROJECT_ID" \
        --zone="$ZONE" \
        --quiet || true
    echo "GKE cluster '$CLUSTER_NAME' deleted (or did not exist)."
    ;;
  aws|AWS)
    CLUSTER_NAME=${1:-"$CLUSTER_NAME_DEFAULT"}
    REGION=${2:-"us-east-2"}
    # Use timeout of 3600 seconds (1 hour) for EKS cluster deletion
    # CloudFormation stack deletion can take a long time
    TIMEOUT_SECONDS=${3:-3600}
    echo "Deleting EKS cluster '$CLUSTER_NAME' in region '$REGION' (timeout: ${TIMEOUT_SECONDS}s)..."
    # Temporarily disable set -e to handle timeout gracefully
    set +e
    timeout "$TIMEOUT_SECONDS" eksctl delete cluster --name "$CLUSTER_NAME" --region "$REGION"
    exit_code=$?
    set -e

    if [ $exit_code -eq 124 ]; then
        echo "⚠️  Warning: EKS cluster deletion timed out after ${TIMEOUT_SECONDS} seconds"
        echo "Cluster deletion may still be in progress in the background"
    elif [ $exit_code -eq 0 ]; then
        echo "EKS cluster '$CLUSTER_NAME' deleted successfully"
    else
        echo "EKS cluster deletion completed with exit code: $exit_code"
    fi
    echo "EKS cluster '$CLUSTER_NAME' deletion process finished."
    ;;
  *)
    echo "Unsupported provider: $PROVIDER"
    echo "Usage: $0 <gcp|aws> [args...]"
    exit 1
    ;;
esac
