#!/bin/bash
set -e

# Configuration (similar to GKE script)
CLUSTER_NAME=${1:-"skypilot-helm-test-eks"}
REGION=${2:-"us-east-2"}
NODE_COUNT=${3:-1}
INSTANCE_TYPE=${4:-"m5.2xlarge"}  # ~8 vCPU, 32GB

echo "Creating EKS cluster..."
echo "Cluster Name: $CLUSTER_NAME"
echo "Region: $REGION"
echo "Node Count: $NODE_COUNT"
echo "Instance Type: $INSTANCE_TYPE"

# Generate a temporary eksctl cluster config
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

# Create EKS cluster (assumes eksctl is installed)
eksctl create cluster -f "$RESOLVED_CONFIG"

# Update kubeconfig
aws eks --region "$REGION" update-kubeconfig --name "$CLUSTER_NAME"

echo "EKS cluster '$CLUSTER_NAME' created successfully!"
echo "Cluster credentials configured for kubectl"
