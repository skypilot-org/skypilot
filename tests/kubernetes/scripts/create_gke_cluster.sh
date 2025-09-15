#!/bin/bash
set -e

# Configuration
CLUSTER_NAME=${1:-"skypilot-helm-test-cluster"}
PROJECT_ID=${2:-$(gcloud config get-value project)}
ZONE=${3:-"us-central1-a"}
NODE_COUNT=${4:-2}
MACHINE_TYPE=${5:-"e2-standard-8"}

echo "Creating GKE cluster..."
echo "Cluster Name: $CLUSTER_NAME"
echo "Project ID: $PROJECT_ID"
echo "Zone: $ZONE"
echo "Node Count: $NODE_COUNT"
echo "Machine Type: $MACHINE_TYPE"

# Create GKE cluster
gcloud container clusters create $CLUSTER_NAME \
    --project=$PROJECT_ID \
    --zone=$ZONE \
    --num-nodes=$NODE_COUNT \
    --machine-type=$MACHINE_TYPE \
    --enable-ip-alias

# Get credentials
echo "Getting cluster credentials..."
gcloud container clusters get-credentials $CLUSTER_NAME --zone=$ZONE --project=$PROJECT_ID

echo "GKE cluster '$CLUSTER_NAME' created successfully!"
echo "Cluster credentials configured for kubectl"
