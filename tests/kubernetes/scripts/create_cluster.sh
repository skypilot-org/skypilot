#!/bin/bash
set -e

# Usage:
#   create_cluster.sh gcp <CLUSTER_NAME> <PROJECT_ID> <ZONE> <NODE_COUNT> <MACHINE_TYPE>
#   create_cluster.sh aws <CLUSTER_NAME> <REGION> <NODE_COUNT> <INSTANCE_TYPE>

# If EKS_VPC_CONFIG_PRIVATE is set, it will be injected verbatim into the eksctl config

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
  aws|AWS)
    CLUSTER_NAME=${1:-"$CLUSTER_NAME_DEFAULT"}
    REGION=${2:-"us-east-2"}
    NODE_COUNT=${3:-1}
    INSTANCE_TYPE=${4:-"m5.2xlarge"}

    echo "Creating AWS EKS cluster..."
    echo "Cluster Name: $CLUSTER_NAME"
    echo "Region: $REGION"
    echo "Node Count: $NODE_COUNT"
    echo "Instance Type: $INSTANCE_TYPE"
    if [ -n "$EKS_VPC_CONFIG_PRIVATE" ]; then
        echo "Using custom VPC configuration from EKS_VPC_CONFIG_PRIVATE"
    else
        echo "Using default VPC configuration (EKS_VPC_CONFIG_PRIVATE not set)"
    fi

    # Check if cluster exists and delete it if present
    echo "Checking if EKS cluster '$CLUSTER_NAME' exists..."
    set +e
    aws eks describe-cluster --region "$REGION" --name "$CLUSTER_NAME" > /dev/null 2>&1
    cluster_exists=$?
    set -e

    if [ $cluster_exists -eq 0 ]; then
        echo "Found existing EKS cluster '$CLUSTER_NAME'. Deleting it first..."
        # Get the directory where this script is located to find delete_cluster.sh
        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        "$SCRIPT_DIR/delete_cluster.sh" aws "$CLUSTER_NAME" "$REGION"
        echo "Waiting for cluster deletion to complete before creating new cluster..."
    else
        echo "No existing EKS cluster found"
    fi

    RESOLVED_CONFIG="/tmp/${CLUSTER_NAME}-eks-cluster-config.yaml"
    # Convert literal \n to actual newlines if EKS_VPC_CONFIG_PRIVATE is set
    if [ -n "$EKS_VPC_CONFIG_PRIVATE" ]; then
        # Use printf to interpret escape sequences like \n
        VPC_CONFIG=$(printf '%b\n' "$EKS_VPC_CONFIG_PRIVATE")
    else
        VPC_CONFIG=""
    fi
    cat > "$RESOLVED_CONFIG" <<EOF
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig

metadata:
  name: ${CLUSTER_NAME}
  region: ${REGION}
${VPC_CONFIG}
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

    # If user provided VPC/subnets via EKS_VPC_CONFIG_PRIVATE, tag those subnets so
    # Service type LoadBalancer can provision internet-facing ELB/NLB.
    if [ -n "$EKS_VPC_CONFIG_PRIVATE" ]; then
        echo "Tagging provided public subnets for internet-facing LoadBalancers..."
        # Convert literal \n to actual newlines for parsing
        VPC_CONFIG_FOR_PARSING=$(printf '%b\n' "$EKS_VPC_CONFIG_PRIVATE")
        # Extract all subnet IDs from the config (deduplicated)
        mapfile -t SUBNET_IDS < <(echo "$VPC_CONFIG_FOR_PARSING" | grep -E 'id:\s*subnet-' | awk '{print $2}' | tr -d '"' | sort -u)
        for subnet_id in "${SUBNET_IDS[@]}"; do
            if [ -n "$subnet_id" ]; then
                echo "Tagging subnet $subnet_id"
                # Check if subnet already has cluster tags to avoid conflicts
                EXISTING_TAGS=$(aws ec2 describe-tags --region "$REGION" --filters "Name=resource-id,Values=$subnet_id" "Name=key,Values=kubernetes.io/cluster/*" --query 'Tags[*].Key' --output text)
                if [ -z "$EXISTING_TAGS" ]; then
                    # No existing cluster tags, safe to add
                    aws ec2 create-tags --region "$REGION" \
                      --resources "$subnet_id" \
                      --tags Key=kubernetes.io/cluster/${CLUSTER_NAME},Value=shared \
                            Key=kubernetes.io/role/elb,Value=1 || true
                else
                    echo "Subnet $subnet_id already has cluster tags: $EXISTING_TAGS"
                    echo "Skipping tagging to avoid conflicts with existing clusters"
                fi
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

    echo "AWS EKS cluster '$CLUSTER_NAME' created successfully!"
    echo "Cluster credentials configured for kubectl"
    ;;
  *)
    echo "Unsupported provider: $PROVIDER"
    echo "Usage: $0 <gcp|aws> ..."
    exit 1
    ;;
esac
