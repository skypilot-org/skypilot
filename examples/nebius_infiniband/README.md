# Interconnecting GPUs in Nebius Managed Service for Kubernetes clusters using InfiniBand with SkyPilot

To accelerate ML, AI and high-performance computing (HPC) workloads that you run in your Managed Service for Kubernetes clusters with GPUs in Nebius, you can interconnect the GPUs using InfiniBand, a high-throughput, low-latency networking standard.


## Interconnect GPUs using InfiniBand

To interconnect the GPUs, you can group your virtual machines with GPUs into a GPU cluster. The GPU clusters are built with InfiniBand secure high-speed networking. Each GPU cluster is created in one of physical InfiniBand fabrics. This is where GPUs interconnected over InfiniBand are located. When creating a GPU cluster, select an InfiniBand fabric for it. Refer to the [Nebius documentation](https://docs.nebius.com/compute/clusters/gpu#fabrics) for how to select the fabric according to the type of GPUs you are going to use.

Here is an example to create a managed service for Kubernetes cluster with InfiniBand enabled for the node group, for more details, refer to the [Nebius documentation](https://docs.nebius.com/kubernetes/gpu/clusters#enable).


1. Create a managed service for Kubernetes cluster.

```bash
export NB_SUBNET_ID=$(nebius vpc subnet list \
  --format json \
  | jq -r '.items[0].metadata.id')

export NB_K8S_CLUSTER_ID=$(nebius mk8s cluster create \
  --name infini \
  --control-plane-version 1.30 \
  --control-plane-subnet-id $NB_SUBNET_ID \
  --control-plane-endpoints-public-endpoint=true \
  --format json | jq -r '.metadata.id')
```

2. To enable InfiniBand for a node group, you need to create a GPU cluster first, then specify the GPU cluster when creating the node group.

```bash
export INFINIBAND_FABRIC=fabric-3
export NB_GPU_CLUSTER_ID=$(nebius compute gpu-cluster create \
  --name gpu-cluster-name \
  --infiniband-fabric $INFINIBAND_FABRIC \
  --format json \
  | jq -r ".metadata.id")

nebius mk8s node-group create \
  --parent-id $NB_K8S_CLUSTER_ID \
  --name high1-ib-group \
  --fixed-node-count 2 \
  --template-resources-platform gpu-h100-sxm \
  --template-resources-preset 8gpu-128vcpu-1600gb \
  --template-gpu-cluster-id $NB_GPU_CLUSTER_ID \
  --template-gpu-settings-drivers-preset cuda12
```

To create a node group with a GPU cluster, you need to specify a compatible preset (number of GPUs and vCPUs, RAM size). The compatible platforms and presets are as below:

| Platform | Presets | Regions |
|---------------|----------|------|
|NVIDIA® H100 NVLink with Intel Sapphire Rapids (gpu-h100-sxm) | 8gpu-128vcpu-1600gb | eu-north1
|NVIDIA® H200 NVLink with Intel Sapphire Rapids (gpu-h200-sxm) | 8gpu-128vcpu-1600gb | eu-north1, eu-west1, us-central1

Now you have a Kubernetes cluster that have the GPUs interconnected using InfiniBand.

## Running NCCL test using SkyPilot

Check the [`nccl.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/nebius_high_performance_network/nccl.yaml) for the complete SkyPilot cluster yaml configurations.

The `image_id` provides the environment setup for [NCCL](https://developer.nvidia.com/nccl) (NVIDIA Collective Communications Library).

To run the NCCL test with InfiniBand support:

```bash
sky launch -c infiniband nccl.yaml
```

SkyPilot will:
1. Schedule the job on a Kubernetes cluster with required GPU nodes
2. Launch Pods and execute the NCCL performance test
3. Output performance metrics showing the benefits of InfiniBand for distributed training

The example result is as below:

```
#                                                              out-of-place                       in-place
#       size         count      type   redop    root     time   algbw   busbw #wrong     time   algbw   busbw #wrong
#        (B)    (elements)                               (us)  (GB/s)  (GB/s)            (us)  (GB/s)  (GB/s)
   536870912     134217728     float     sum      -1   2432.7  220.69  413.79      0   2382.4  225.35  422.54      0
  1073741824     268435456     float     sum      -1   4523.3  237.38  445.09      0   4518.9  237.61  445.52      0
  2147483648     536870912     float     sum      -1   8785.8  244.43  458.30      0   8787.2  244.39  458.23      0
  4294967296    1073741824     float     sum      -1    17404  246.79  462.73      0    17353  247.50  464.07      0
  8589934592    2147483648     float     sum      -1    34468  249.21  467.28      0    34525  248.80  466.51      0
# Out of bounds values : 0 OK
# Avg bus bandwidth    : 450.404
```

> **NOTE:**
>
> To run NCCL tests without InfiniBand, you can create the node group [without the GPU cluster](https://docs.nebius.com/kubernetes/node-groups/manage).
> Then launch a cluster with `nccl.yaml` by passing an env:
> ```bash
> sky launch -c no_infiniband --env USE_IB=false nccl.yaml
> ```
