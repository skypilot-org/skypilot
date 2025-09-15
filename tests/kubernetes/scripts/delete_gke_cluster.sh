#!/bin/bash
set -e

# Configuration
CLUSTER_NAME=${1:-"skypilot-helm-test-cluster"}
PROJECT_ID=${2:-$(gcloud config get-value project)}
ZONE=${3:-"us-central1-a"}

echo "Deleting GKE cluster..."
echo "Cluster Name: $CLUSTER_NAME"
echo "Project ID: $PROJECT_ID"
echo "Zone: $ZONE"

# Delete GKE cluster
gcloud container clusters delete $CLUSTER_NAME \
    --project=$PROJECT_ID \
    --zone=$ZONE \
    --quiet || true

echo "GKE cluster '$CLUSTER_NAME' deleted successfully!"
