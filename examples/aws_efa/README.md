# Using AWS Elastic Fabric Adapter (EFA) with SkyPilot

## What is AWS EFA?

Elastic Fabric Adapter (EFA) is a network interface for Amazon EC2 instances that enables high-performance computing and machine learning applications to scale efficiently in the cloud. EFA provides lower latency, higher throughput, and OS-bypass capabilities that are critical for communication-intensive workloads such as distributed training.


## Setting up EFA on EKS
For complete details on setting up EFA with Amazon EKS, refer to the [official AWS documentation](https://docs.aws.amazon.com/eks/latest/userguide/node-efa.html).


To check if EFA is enabled, run:
```
kubectl get nodes "-o=custom-columns=NAME:.metadata.name,INSTANCETYPE:.metadata.labels.node\.kubernetes\.io/instance-type,GPU:.status.allocatable.nvidia\.com/gpu,EFA:.status.allocatable.vpc\.amazonaws\.com/efa"
```
You can expect a sample output like:
```
NAME                           INSTANCETYPE    GPU   EFA
hyperpod-i-055aeff9546187dee   ml.g5.8xlarge   1     1
hyperpod-i-09662f64f615c96f5   ml.g5.8xlarge   1     1
hyperpod-i-099e2a84aba621d52   ml.g5.8xlarge   1     1
...
```

### Turn on EFA in SkyPilot YAML
This example demonstrates how to configure SkyPilot to use EFA for NCCL performance testing on AWS. Let's break down the `nccl_efa.yaml` file component by component:

```yaml
config:
  kubernetes:
    pod_config:
      spec:
        containers:
        - resources:
            limits:
              vpc.amazonaws.com/efa: 4
              nvidia.com/gpu: 8
            requests:
              vpc.amazonaws.com/efa: 4
              nvidia.com/gpu: 8
```

This section is important for EFA integration:

- `config.kubernetes.pod_config`: Provides Kubernetes-specific pod configuration
- `spec.containers[0].resources`: Defines resource requirements
  - `limits.vpc.amazonaws.com/efa: 4`: Limits the Pod to use 4 EFA devices
  - `limits.nvidia.com/gpu: 8`: Limits the Pod to use 8 GPUs
  - `requests.vpc.amazonaws.com/efa: 4`: Requests 4 EFA devices for the Pod
  - `requests.nvidia.com/gpu: 8`: Requests 8 GPUs for the Pod


The `vpc.amazonaws.com/efa` resource type is exposed by the AWS EFA device plugin in Kubernetes. 
To see how many EFA are available for each instance types that have EFA, see the [Network cards](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-eni.html#network-cards) list in the Amazon EC2 User Guide.

To see how many GPUs are available for each instance types, see [EC2 Instance Types](https://aws.amazon.com/cn/ec2/instance-types/) and select Accelerated Computing.

Update the EFA and GPU number in the `nccl_efa.yaml` for your instance type.

## Running NCCL test with EFA using SkyPilot

```yaml
name: nccl-test-efa

resources:
  cloud: kubernetes
  accelerators: A100:8
  cpus: 90+
  image_id: docker:public.ecr.aws/hpc-cloud/nccl-tests:latest

num_nodes: 2

envs:
  USE_EFA: "true"

run: |
  if [ "${SKYPILOT_NODE_RANK}" == "0" ]; then
    echo "Head node"

    # NP should be number of GPUs per node times number of nodes
    NP=$(($SKYPILOT_NUM_GPUS_PER_NODE * $SKYPILOT_NUM_NODES))

    # Append :${SKYPILOT_NUM_GPUS_PER_NODE} to each IP as slots
    nodes=""
    for ip in $SKYPILOT_NODE_IPS; do
      nodes="${nodes}${ip}:${SKYPILOT_NUM_GPUS_PER_NODE},"
    done
    nodes=${nodes::-1}
    echo "All nodes: ${nodes}"

    # Set environment variables
    export PATH=$PATH:/usr/local/cuda-12.2/bin:/opt/amazon/efa/bin:/usr/bin
    export LD_LIBRARY_PATH=/usr/local/cuda-12.2/lib64:/opt/amazon/openmpi/lib:/opt/nccl/build/lib:/opt/amazon/efa/lib:/opt/aws-ofi-nccl/install/lib:/usr/local/nvidia/lib:$LD_LIBRARY_PATH
    export NCCL_HOME=/opt/nccl
    export CUDA_HOME=/usr/local/cuda-12.2
    export NCCL_DEBUG=INFO
    export NCCL_BUFFSIZE=8388608
    export NCCL_P2P_NET_CHUNKSIZE=524288
    export NCCL_TUNER_PLUGIN=/opt/aws-ofi-nccl/install/lib/libnccl-ofi-tuner.so

    if [ "${USE_EFA}" == "true" ]; then
      export FI_PROVIDER="efa"
    else
      export FI_PROVIDER=""
    fi

    /opt/amazon/openmpi/bin/mpirun \
      --allow-run-as-root \
      --tag-output \
      -H $nodes \
      -np $NP \
      -N $SKYPILOT_NUM_GPUS_PER_NODE \
      --bind-to none \
      -x FI_PROVIDER \
      -x PATH \
      -x LD_LIBRARY_PATH \
      -x NCCL_DEBUG=INFO \
      -x NCCL_BUFFSIZE \
      -x NCCL_P2P_NET_CHUNKSIZE \
      -x NCCL_TUNER_PLUGIN \
      --mca pml ^cm,ucx \
      --mca btl tcp,self \
      --mca btl_tcp_if_exclude lo,docker0,veth_def_agent \
      /opt/nccl-tests/build/all_reduce_perf \
      -b 8 \
      -e 2G \
      -f 2 \
      -g 1 \
      -c 5 \
      -w 5 \
      -n 100
  else
    echo "Worker nodes"
  fi
```

The image_id provides the environment setup for NCCL (NVIDIA Collective Communications Library) and EFA (Elastic Fabric Adapter):

- [NCCL](https://developer.nvidia.com/nccl) is NVIDIA's library for multi-GPU and multi-node collective communication operations
- [EFA](https://aws.amazon.com/hpc/efa/) is AWS's network interface for high-performance computing applications
- [CUDA](https://developer.nvidia.com/cuda-toolkit) is NVIDIA's parallel computing platform and programming model for GPUs


To run the NCCL test with EFA support:

```bash
sky launch -c efa examples/aws_efa/nccl_efa.yaml
```

SkyPilot will:
1. Schedule the task on a Kubernetes cluster with EFA-enabled nodes
2. Launch containers with the required EFA devices
3. Execute the NCCL performance test with EFA networking
4. Output performance metrics showing the benefits of EFA for distributed training


To verify that EFA is successfully enabled, you can ssh to the head node with `ssh efa` and run:
```
$ fi_info -p efa -t FI_EP_RDM
provider: efa
    fabric: efa
    domain: rdmap16s27-rdm
    version: 122.0
    type: FI_EP_RDM
    protocol: FI_PROTO_EFA
provider: efa
    fabric: efa
    domain: rdmap32s27-rdm
    version: 122.0
    type: FI_EP_RDM
    protocol: FI_PROTO_EFA
...
```

To run the NCCL test without EFA support, set `envs.USE_EFA: "false"` in `nccl_efa.yaml` and run:
```bash
sky launch -c efa examples/aws_efa/nccl_efa.yaml
```

## Test results

Below is a side-by-side view of the effective bus bandwidth (out-of-place busbw) that NCCL reports for the same HyperPod cluster (`2 * p4d.24xlarge`) run with AWS EFA enabled vs. disabled.

The `Speed-up` column is calculated by `busbw EFA (GB/s) / busbw non-EFA (GB/s)`.

| Message Size | busbw EFA (GB/s) | busbw non-EFA (GB/s) | Speed-up |
|--------------|-------------|-------------|---------------|
| 8 B          | 0           | 0           | -             |
| 16 B         | 0           | 0           | -             |
| 32 B         | 0           | 0           | -             |
| 64 B         | 0           | 0           | -             |
| 128 B        | 0           | 0           | -             |
| 256 B        | 0           | 0           | -             |
| 512 B        | 0.01        | 0.01        | 1 x           |
| 1 KB         | 0.01        | 0.01        | 1 x           |
| 2 KB         | 0.02        | 0.02        | 1 x           |
| 4 KB         | 0.04        | 0.05        | 0.8 x         |
| 8 KB         | 0.08        | 0.06        | 1.3 x         |
| 16 KB        | 0.14        | 0.06        | 2.3 x         |
| 32 KB        | 0.25        | 0.17        | 1.4 x         |
| 64 KB        | 0.49        | 0.23        | 2.1 x         |
| 128 KB       | 0.97        | 0.45        | 2.1 x         |
| 256 KB       | 1.86        | 0.68        | 2.7 x         |
| 512 KB       | 3.03        | 1.01        | 3 x           |
| 1 MB         | 4.61        | 1.65        | 2.8 x         |
| 2 MB         | 6.5         | 1.75        | 3.7 x         |
| 4 MB         | 8.91        | 2.39        | 3.7 x         |
| 8 MB         | 10.5        | 2.91        | 3.6 x         |
| 16 MB        | 19.03       | 3.22        | 5.9 x         |
| 32 MB        | 31.85       | 3.58        | 8.9 x         |
| 64 MB        | 44.37       | 3.85        | 11.5 x        |
| 128 MB       | 54.94       | 3.87        | 14.2 x        |
| 256 MB       | 65.46       | 3.94        | 16.6 x        |
| 512 MB       | 71.83       | 4.04        | 17.7 x        |
| 1 GB         | 75.34       | 4.08        | 18.4 x        |
| 2 GB         | 77.35       | 4.13        | 18.7 x        |

### What stands out

| Range         | Observation                                                                                                                                     |
|---------------|-------------------------------------------------------------------------------------------------------------------------------------------------|
| ≤ 256 KB       | Virtually no difference — bandwidth is dominated by software/latency overhead and doesn’t reach the network’s limits. |
| 512 KB – 16 MB | EFA gradually pulls ahead, hitting ~3–6 × by a few MB. |
| ≥ 32 MB       | The fabric really kicks in: ≥ 8 x at 32 MB, climbing to ~18 x for 1–2 GB messages. Non-EFA tops out around 4 GB/s, while EFA pushes ≈ 77 GB/s. |

From the above results and brief summary, we can see that EFA provides much higher throughput than the TCP transport traditionally used in cloud-based HPC (High Performance Computing) systems. It enhances the performance of inter-instance communication that is critical for scaling AI/ML and HPC applications.