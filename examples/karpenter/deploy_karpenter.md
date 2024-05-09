# Deploying a Karpenter autoscaling cluster on EKS

This guide is adapted from the [official Karpenter documentation](https://karpenter.sh/docs/getting-started/). It will guide you through deploying a Karpenter cluster on EKS with an autoscaling node pool of T4 GPUs (g4dn.xlarge). 

## Install Pre-requisites

Karpenter is installed in clusters with a Helm chart.

Install these tools before proceeding:

1. [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2-linux.html)
2. `kubectl` - [the Kubernetes CLI](https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/)
3. `eksctl` (>= v0.169.0) - [the CLI for AWS EKS](https://docs.aws.amazon.com/eks/latest/userguide/eksctl.html)
4. `helm` - [the package manager for Kubernetes](https://helm.sh/docs/intro/install/)

[Configure the AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-quickstart.html)
with a user that has sufficient privileges to create an EKS cluster. Verify that the CLI can
authenticate properly by running `aws sts get-caller-identity`.

## Define environment variables

```console
export KARPENTER_NAMESPACE="kube-system"
export KARPENTER_VERSION="0.36.1"
export K8S_VERSION="1.29"

export AWS_PARTITION="aws" # if you are not using standard partitions, you may need to configure to aws-cn / aws-us-gov
export CLUSTER_NAME="${USER}-karpenter-demo"
export AWS_DEFAULT_REGION="us-west-2"
export AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
export TEMPOUT="$(mktemp)"
export ARM_AMI_ID="$(aws ssm get-parameter --name /aws/service/eks/optimized-ami/${K8S_VERSION}/amazon-linux-2-arm64/recommended/image_id --query Parameter.Value --output text)"
export AMD_AMI_ID="$(aws ssm get-parameter --name /aws/service/eks/optimized-ami/${K8S_VERSION}/amazon-linux-2/recommended/image_id --query Parameter.Value --output text)"
export GPU_AMI_ID="$(aws ssm get-parameter --name /aws/service/eks/optimized-ami/${K8S_VERSION}/amazon-linux-2-gpu/recommended/image_id --query Parameter.Value --output text)"
```

## Create cluster

Create a basic cluster with `eksctl`.
The following cluster configuration will:

* Use CloudFormation to set up the infrastructure needed by the EKS cluster.
* Create a Kubernetes service account and AWS IAM Role, and associate them using IRSA to let Karpenter launch instances.
* Add the Karpenter node role to the aws-auth configmap to allow nodes to connect.
* Use [AWS EKS managed node groups](https://docs.aws.amazon.com/eks/latest/userguide/managed-node-groups.html) for the kube-system and karpenter namespaces.
* Set KARPENTER_IAM_ROLE_ARN variables.
* Create a role to allow spot instances.
* Run Helm to install Karpenter


```
curl -fsSL https://raw.githubusercontent.com/aws/karpenter-provider-aws/v"${KARPENTER_VERSION}"/website/content/en/preview/getting-started/getting-started-with-karpenter/cloudformation.yaml  > "${TEMPOUT}" \
&& aws cloudformation deploy \
  --stack-name "Karpenter-${CLUSTER_NAME}" \
  --template-file "${TEMPOUT}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides "ClusterName=${CLUSTER_NAME}"

eksctl create cluster -f - <<EOF
---
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig
metadata:
  name: ${CLUSTER_NAME}
  region: ${AWS_DEFAULT_REGION}
  version: "${K8S_VERSION}"
  tags:
    karpenter.sh/discovery: ${CLUSTER_NAME}

iam:
  withOIDC: true
  podIdentityAssociations:
  - namespace: "${KARPENTER_NAMESPACE}"
    serviceAccountName: karpenter
    roleName: ${CLUSTER_NAME}-karpenter
    permissionPolicyARNs:
    - arn:${AWS_PARTITION}:iam::${AWS_ACCOUNT_ID}:policy/KarpenterControllerPolicy-${CLUSTER_NAME}

iamIdentityMappings:
- arn: "arn:${AWS_PARTITION}:iam::${AWS_ACCOUNT_ID}:role/KarpenterNodeRole-${CLUSTER_NAME}"
  username: system:node:{{EC2PrivateDNSName}}
  groups:
  - system:bootstrappers
  - system:nodes
  ## If you intend to run Windows workloads, the kube-proxy group should be specified.
  # For more information, see https://github.com/aws/karpenter/issues/5099.
  # - eks:kube-proxy-windows

managedNodeGroups:
- instanceType: m5.large
  amiFamily: AmazonLinux2
  name: ${CLUSTER_NAME}-ng
  desiredCapacity: 2
  minSize: 1
  maxSize: 10

addons:
- name: eks-pod-identity-agent
EOF

export CLUSTER_ENDPOINT="$(aws eks describe-cluster --name "${CLUSTER_NAME}" --query "cluster.endpoint" --output text)"
export KARPENTER_IAM_ROLE_ARN="arn:${AWS_PARTITION}:iam::${AWS_ACCOUNT_ID}:role/${CLUSTER_NAME}-karpenter"

echo "${CLUSTER_ENDPOINT} ${KARPENTER_IAM_ROLE_ARN}"
```
Then run:
```
aws iam create-service-linked-role --aws-service-name spot.amazonaws.com || true
# If the role has already been successfully created, you will see:
# An error occurred (InvalidInput) when calling the CreateServiceLinkedRole operation: Service role name AWSServiceRoleForEC2Spot has been taken in this account, please try a different suffix.
```

## Install Karpenter

Once your EKS cluster is up and running, you can install Karpenter with Helm.
```console
helm registry logout public.ecr.aws

helm upgrade --install karpenter oci://public.ecr.aws/karpenter/karpenter --version "${KARPENTER_VERSION}" --namespace "${KARPENTER_NAMESPACE}" --create-namespace \
  --set "settings.clusterName=${CLUSTER_NAME}" \
  --set "settings.interruptionQueue=${CLUSTER_NAME}" \
  --set controller.resources.requests.cpu=1 \
  --set controller.resources.requests.memory=1Gi \
  --set controller.resources.limits.cpu=1 \
  --set controller.resources.limits.memory=1Gi \
  --wait
```

## Create nodepool

We will now define a nodepool of on-demand T4 GPUs along with a EC2NodeClass to manage the nodepool. We will also attach a 100GiB gp3 volume to each node in the nodepool to make sure we have enough space for GPU images.

```console
cat <<EOF | envsubst | kubectl apply -f -
apiVersion: karpenter.sh/v1beta1
kind: NodePool
metadata:
  name: default
spec:
  template:
    spec:
      requirements:
        - key: kubernetes.io/arch
          operator: In
          values: ["amd64"]
        - key: kubernetes.io/os
          operator: In
          values: ["linux"]
        - key: karpenter.sh/capacity-type
          operator: In
          values: ["on-demand"]
        - key: karpenter.k8s.aws/instance-gpu-name
          operator: In
          values: ["t4"]
      nodeClassRef:
        apiVersion: karpenter.k8s.aws/v1beta1
        kind: EC2NodeClass
        name: default
  limits:
    cpu: 1000
    nvidia.com/gpu: 16
  disruption:
    consolidationPolicy: WhenEmpty
    consolidateAfter: 600s
    expireAfter: 720h # 30 * 24h = 720h
---
apiVersion: karpenter.k8s.aws/v1beta1
kind: EC2NodeClass
metadata:
  name: default
spec:
  amiFamily: AL2 # Amazon Linux 2
  role: "KarpenterNodeRole-${CLUSTER_NAME}" # replace with your cluster name
  subnetSelectorTerms:
    - tags:
        karpenter.sh/discovery: "${CLUSTER_NAME}" # replace with your cluster name
  securityGroupSelectorTerms:
    - tags:
        karpenter.sh/discovery: "${CLUSTER_NAME}" # replace with your cluster name
  amiSelectorTerms:
    - id: "${GPU_AMI_ID}" # <- GPU Optimized AMD AMI
  blockDeviceMappings:
    - deviceName: /dev/xvda
      ebs:
        volumeType: gp3
        volumeSize: 100Gi
        deleteOnTermination: true
EOF
```
You should see the following output:
```
nodepool.karpenter.sh/default created
ec2nodeclass.karpenter.k8s.aws/default created
```

## Deploy NVIDIA device plugin

To allow pods to use the GPUs, you need to deploy the NVIDIA device plugin. This runs as a DaemonSet on the cluster and exposes the GPUs on the nodes as nvidia.com/gpu resources.

```
kubectl create -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.15.0/deployments/static/nvidia-device-plugin.yml
```

<details>
<summary> [Optional] If you prefer, you can also deploy NVIDIA GPU Operator instead of just the device plugin.</summary>

```console
kubectl create ns gpu-operator
kubectl label --overwrite ns gpu-operator pod-security.kubernetes.io/enforce=privileged

helm repo add nvidia https://helm.ngc.nvidia.com/nvidia && helm repo update

helm install --wait --generate-name \
    -n gpu-operator --create-namespace \
    nvidia/gpu-operator
```
</details>


## 🚀 Your EKS + Karpenter cluster is ready!
That's it! You can use SkyPilot to deploy your workloads on the cluster now! Run `sky check` to verify that Kubernetes is enabled and make sure your `~/.sky/config.yaml` contains:
```
kubernetes:
  autoscaler: karpenter
  provision_timeout: 900    # A long provision timeout to allow Karpenter to scale up the GPU nodes while SkyPilot waits
```

## Teardown

To delete the cluster and all resources created in this guide, run:

```console
helm uninstall karpenter --namespace "${KARPENTER_NAMESPACE}"
aws cloudformation delete-stack --stack-name "Karpenter-${CLUSTER_NAME}"
aws ec2 describe-launch-templates --filters "Name=tag:karpenter.k8s.aws/cluster,Values=${CLUSTER_NAME}" |
    jq -r ".LaunchTemplates[].LaunchTemplateName" |
    xargs -I{} aws ec2 delete-launch-template --launch-template-name {}
eksctl delete cluster --name "${CLUSTER_NAME}"
```