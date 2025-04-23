# Using AWS Elastic Fabric Adapter (EFA) with SkyPilot

## What is AWS EFA?

Elastic Fabric Adapter (EFA) is a network interface for Amazon EC2 instances that enables high-performance computing and machine learning applications to scale efficiently in the cloud. EFA provides lower latency, higher throughput, and OS-bypass capabilities that are critical for communication-intensive workloads such as distributed training.


### Setting up EFA cluster 
For complete details on setting up EFA with Amazon EKS, refer to the [official AWS documentation](https://docs.aws.amazon.com/eks/latest/userguide/node-efa.html).


To check if EFA is enabled, run 
```
kubectl get nodes "-o=custom-columns=NAME:.metadata.name,INSTANCETYPE:.metadata.labels.node\.kubernetes\.io/instance-type,GPU:.status.allocatable.nvidia\.com/gpu,EFA:.status.allocatable.vpc\.amazonaws\.com/efa"
```
you can expect a sample output like
```
NAME                           INSTANCETYPE    GPU   EFA
hyperpod-i-055aeff9546187dee   ml.g5.8xlarge   1     1
hyperpod-i-09662f64f615c96f5   ml.g5.8xlarge   1     1
hyperpod-i-099e2a84aba621d52   ml.g5.8xlarge   1     1
...
```

### Kubernetes-Specific Configuration
This example demonstrates how to configure SkyPilot to use EFA for NCCL performance testing on AWS. Let's break down the `nccl_efa.yaml` file component by component:

```yaml
config:
  kubernetes:
    pod_config:
      spec:
        containers:
        - resources:
            limits:
              vpc.amazonaws.com/efa: 2
            requests:
              vpc.amazonaws.com/efa: 2
```

This section is important for EFA integration:

- `config.kubernetes.pod_config`: Provides Kubernetes-specific pod configuration
- `spec.containers[0].resources`: Defines resource requirements
  - `limits.vpc.amazonaws.com/efa: 2`: Limits the pod to using 2 EFA devices
  - `requests.vpc.amazonaws.com/efa: 2`: Requests 2 EFA devices for the pod

The `vpc.amazonaws.com/efa` resource type is exposed by the AWS EFA device plugin in Kubernetes. 

### Basic Task Configuration

```yaml
name: nccl-test

resources:
  cloud: kubernetes
  accelerators: A100:2
  cpus: 64
  image_id: docker:public.ecr.aws/hpc-cloud/nccl-tests:latest

num_nodes: 2
```

The image_id provides the environment setup for NCCL (NVIDIA Collective Communications Library) and EFA (Elastic Fabric Adapter):

- [NCCL](https://developer.nvidia.com/nccl) is NVIDIA's library for multi-GPU and multi-node collective communication operations
- [EFA](https://aws.amazon.com/hpc/efa/) is AWS's network interface for high-performance computing applications
- [CUDA](https://developer.nvidia.com/cuda-toolkit) is NVIDIA's parallel computing platform and programming model for GPUs




## Running the EFA Test

To run the NCCL test with EFA support:

```bash
sky launch examples/aws_efa/nccl_efa.yaml
```

SkyPilot will:
1. Schedule the task on a Kubernetes cluster with EFA-enabled nodes
2. Launch containers with the required EFA devices
3. Execute the NCCL performance test with EFA networking
4. Output performance metrics showing the benefits of EFA for distributed training


To verify that EFA is successfully used, you can ssh to the head node by checking `sky status` and run
```
$ fi_info -p efa -t FI_EP_RDM

provider: efa
fabric: EFA-fe80::94:3dff:fe89:1b70
domain: efa_0-rdm
version: 2.0
type: FI_EP_RDM
protocol: FI_PROTO_EFA
```