#!/bin/bash
set -e

# Usage:
#   create_cluster.sh gcp <CLUSTER_NAME> <PROJECT_ID> <ZONE> <NODE_COUNT> <MACHINE_TYPE>
#   create_cluster.sh eks <CLUSTER_NAME> <REGION> <NODE_COUNT> <INSTANCE_TYPE>

PROVIDER=${1:-"gcp"}
shift || true

case "$PROVIDER" in
  gcp|GCP)
    CLUSTER_NAME=${1:-"skypilot-helm-test-cluster"}
    PROJECT_ID=${2:-$(gcloud config get-value project)}
    ZONE=${3:-"us-central1-a"}
    NODE_COUNT=${4:-1}
    MACHINE_TYPE=${5:-"e2-standard-8"}

    echo "Creating GKE cluster..."
    echo "Cluster Name: $CLUSTER_NAME"
    echo "Project ID: $PROJECT_ID"
    echo "Zone: $ZONE"
    echo "Node Count: $NODE_COUNT"
    echo "Machine Type: $MACHINE_TYPE"

    gcloud container clusters create "$CLUSTER_NAME" \
        --project="$PROJECT_ID" \
        --zone="$ZONE" \
        --num-nodes="$NODE_COUNT" \
        --machine-type="$MACHINE_TYPE" \
        --enable-ip-alias

    echo "Getting cluster credentials..."
    gcloud container clusters get-credentials "$CLUSTER_NAME" --zone="$ZONE" --project="$PROJECT_ID"

    echo "GKE cluster '$CLUSTER_NAME' created successfully!"
    echo "Cluster credentials configured for kubectl"
    ;;
  eks|EKS)
    CLUSTER_NAME=${1:-"skypilot-helm-test-eks"}
    REGION=${2:-"us-east-2"}
    NODE_COUNT=${3:-1}
    INSTANCE_TYPE=${4:-"m5.2xlarge"}

    echo "Creating EKS cluster..."
    echo "Cluster Name: $CLUSTER_NAME"
    echo "Region: $REGION"
    echo "Node Count: $NODE_COUNT"
    echo "Instance Type: $INSTANCE_TYPE"

    RESOLVED_CONFIG="/tmp/${CLUSTER_NAME}-eks-cluster-config.yaml"
    cat > "$RESOLVED_CONFIG" <<EOF
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig

metadata:
  name: ${CLUSTER_NAME}
  region: ${REGION}

managedNodeGroups:
  - name: ng-default
    instanceType: ${INSTANCE_TYPE}
    desiredCapacity: ${NODE_COUNT}
    amiFamily: AmazonLinux2
EOF

    echo "Using generated config at $RESOLVED_CONFIG"
    eksctl create cluster -f "$RESOLVED_CONFIG"

    aws eks --region "$REGION" update-kubeconfig --name "$CLUSTER_NAME"

    echo "EKS cluster '$CLUSTER_NAME' created successfully!"
    echo "Cluster credentials configured for kubectl"
    ;;
  *)
    echo "Unsupported provider: $PROVIDER"
    echo "Usage: $0 <gcp|eks> ..."
    exit 1
    ;;
esac
