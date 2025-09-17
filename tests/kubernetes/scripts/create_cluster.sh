#!/bin/bash
set -e

# Usage:
#   create_cluster.sh gcp <CLUSTER_NAME> <PROJECT_ID> <ZONE> <NODE_COUNT> <MACHINE_TYPE>
#   create_cluster.sh eks <CLUSTER_NAME> <REGION> <NODE_COUNT> <INSTANCE_TYPE>

# If EKS_VPC_CONFIG is set, it will be injected verbatim into the eksctl config

PROVIDER=${1:-"gcp"}
shift || true

# Global defaults
CLUSTER_NAME_DEFAULT="skypilot-helm-test-cluster"

case "$PROVIDER" in
  gcp|GCP)
    CLUSTER_NAME=${1:-"$CLUSTER_NAME_DEFAULT"}
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
    CLUSTER_NAME=${1:-"$CLUSTER_NAME_DEFAULT"}
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
${EKS_VPC_CONFIG}
iam:
  withOIDC: true
managedNodeGroups:
  - name: ng-default
    instanceType: ${INSTANCE_TYPE}
    desiredCapacity: ${NODE_COUNT}
    amiFamily: AmazonLinux2
addons:
  - name: aws-ebs-csi-driver
EOF

    echo "Using generated config at $RESOLVED_CONFIG"
    eksctl create cluster -f "$RESOLVED_CONFIG"

    aws eks --region "$REGION" update-kubeconfig --name "$CLUSTER_NAME"

    # If user provided VPC/subnets via EKS_VPC_CONFIG, tag those subnets so
    # Service type LoadBalancer can provision internet-facing ELB/NLB.
    if [ -n "$EKS_VPC_CONFIG" ]; then
        echo "Tagging provided public subnets for internet-facing LoadBalancers..."
        # Extract all subnet IDs from the config (deduplicated)
        mapfile -t SUBNET_IDS < <(echo "$EKS_VPC_CONFIG" | grep -E 'id:\s*subnet-' | awk '{print $2}' | tr -d '"' | sort -u)
        for subnet_id in "${SUBNET_IDS[@]}"; do
            if [ -n "$subnet_id" ]; then
                echo "Tagging subnet $subnet_id"
                aws ec2 create-tags --region "$REGION" \
                  --resources "$subnet_id" \
                  --tags Key=kubernetes.io/cluster/${CLUSTER_NAME},Value=shared \
                        Key=kubernetes.io/role/elb,Value=1 || true
                # Ensure instances launched in these subnets can get public IPs
                aws ec2 modify-subnet-attribute --region "$REGION" \
                  --subnet-id "$subnet_id" --map-public-ip-on-launch || true
            fi
        done
    fi

    # Set default gp3 StorageClass (EBS CSI)
    echo "Applying default gp3 StorageClass (EBS CSI)..."
    cat > /tmp/eks-gp3-sc.yaml <<'SCYAML'
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: gp3
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: ebs.csi.aws.com
parameters:
  type: gp3
reclaimPolicy: Delete
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: true
SCYAML
    kubectl apply -f /tmp/eks-gp3-sc.yaml

    echo "EKS cluster '$CLUSTER_NAME' created successfully!"
    echo "Cluster credentials configured for kubectl"
    ;;
  *)
    echo "Unsupported provider: $PROVIDER"
    echo "Usage: $0 <gcp|eks> ..."
    exit 1
    ;;
esac
