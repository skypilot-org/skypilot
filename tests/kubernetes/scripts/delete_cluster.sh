#!/bin/bash
set -e

# Usage: delete_cluster.sh <provider> [args...]
# Providers:
#  - gcp: args => <CLUSTER_NAME> <PROJECT_ID> <ZONE>
#  - eks: args => <CLUSTER_NAME> <REGION>

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
  eks|EKS)
    CLUSTER_NAME=${1:-"$CLUSTER_NAME_DEFAULT"}
    REGION=${2:-"us-east-2"}
    echo "Deleting EKS cluster '$CLUSTER_NAME' in region '$REGION'..."
    eksctl delete cluster --name "$CLUSTER_NAME" --region "$REGION" || true
    echo "EKS cluster '$CLUSTER_NAME' deleted (or did not exist)."
    ;;
  *)
    echo "Unsupported provider: $PROVIDER"
    echo "Usage: $0 <gcp|eks> [args...]"
    exit 1
    ;;
esac
