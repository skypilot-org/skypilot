# Using InfiniBand in Nebius with SkyPilot

## Setup InfiniBand with a single SkyPilot configuration

SkyPilot provides the `network_tier: best` configuration option that automatically enables InfiniBand support on Nebius Kubernetes clusters and Nebius VMs. This eliminates the need for manual configuration of security contexts and environment variables.

### InfiniBand on Nebius managed Kubernetes clusters

Simply add ``network_tier: best`` to your resources specification:

```yaml
resources:
  infra: k8s
  accelerators: H100:8
  network_tier: best
```

To create a Nebius Kubernetes cluster with InfiniBand enabled, check the [Appendix](#appendix-creating-a-nebius-kubernetes-cluster-with-infiniband-enabled).

### End-to-end Example

Check the [`nccl_network_tier.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/nebius_infiniband/nccl_network_tier.yaml) for a complete example using the simplified configuration:

```bash
sky launch -c nccl_network_tier nccl_network_tier.yaml
```

This enables the InfiniBand for inter-GPU communication, and SkyPilot will automatically setup the environment variables for you.

<details>
<summary>Equivalent way to turn on InfiniBand manually</summary>

With Nebius managed Kubernetes cluster, you can also turn on InfiniBand manually:

1. Set the following config in your SkyPilot task YAML to enable InfiniBand:

```yaml
config:
  kubernetes:
    pod_config:
      spec:
        containers:
        - securityContext:
            capabilities:
              add:
              - IPC_LOCK
```

2. Configure the environment variables in your task:

```bash
run: |
  export NCCL_IB_HCA=mlx5
  export UCX_NET_DEVICES=mlx5_0:1,mlx5_1:1,mlx5_2:1,mlx5_3:1,mlx5_4:1,mlx5_5:1,mlx5_6:1,mlx5_7:1
  ... your own run script ...
```

Check more details in [`nccl.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/nebius_infiniband/nccl.yaml)

</details>


### Running NCCL test using SkyPilot

Check the [`nccl_network_tier.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/nebius_infiniband/nccl_network_tier.yaml) for the complete SkyPilot cluster yaml configurations.

The `image_id` provides the environment setup for [NCCL](https://developer.nvidia.com/nccl) (NVIDIA Collective Communications Library).

To run the NCCL test with InfiniBand support:

```bash
sky launch -c infiniband nccl_network_tier.yaml
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

> **NOTE:** To run NCCL tests without InfiniBand, you can create the node group [without the GPU cluster](https://docs.nebius.com/kubernetes/node-groups/manage). Then launch a cluster with `nccl_no_ib.yaml` with the config field removed:

```bash
sky launch -c no_infiniband nccl_no_ib.yaml
```


## InfiniBand on Nebius VMs with SkyPilot

While the previous section covered InfiniBand setup for managed Kubernetes service, you can also enable InfiniBand directly on Nebius VMs. This approach gives you more flexibility and control over your infrastructure. For detailed instructions, refer to the [Nebius documentation](https://docs.nebius.com/compute/clusters/gpu).

### Automatic InfiniBand Setup with SkyPilot

SkyPilot simplifies the process of setting up InfiniBand-enabled GPU clusters on Nebius VMs. When you launch a cluster with the appropriate configurations, SkyPilot will automatically create a GPU cluster with InfiniBand support and add VMs to the GPU cluster.

To enable automatic InfiniBand setup, you can simply choose the best network in your SkyPilot YAML:

```yaml
resources:
  network_tier: best
```

SkyPilot will automatically configure the InfiniBand with the correct fabric for you. (Note that, Infiniband is only supported by two GPU types, H100:8 and H200:8. Refer to [Nebius Docs](https://docs.nebius.com/compute/clusters/gpu#fabrics)).

### Running Performance Tests

You can verify your InfiniBand setup by running either of these tests:

1. NCCL Performance Test (with specific docker image):

```bash
sky launch -c infiniband nccl_vm_ib.yaml
```

Result example:

```
#                                                              out-of-place                       in-place          
#       size         count      type   redop    root     time   algbw   busbw #wrong     time   algbw   busbw #wrong
#        (B)    (elements)                               (us)  (GB/s)  (GB/s)            (us)  (GB/s)  (GB/s)       
   536870912     134217728     float     sum      -1   2399.1  223.78  419.59      0   2354.3  228.04  427.57      0
  1073741824     268435456     float     sum      -1   4469.9  240.22  450.41      0   4463.1  240.58  451.09      0
  2147483648     536870912     float     sum      -1   8678.7  247.44  463.96      0   8667.1  247.77  464.57      0
  4294967296    1073741824     float     sum      -1    17053  251.86  472.24      0    17112  250.99  470.60      0
  8589934592    2147483648     float     sum      -1    33792  254.20  476.62      0    33735  254.63  477.42      0
# Out of bounds values : 0 OK
# Avg bus bandwidth    : 457.407 
```

2. NCCL Performance Test (setting up NCCL):

```bash
sky launch -c infiniband nccl_no_docker_ib.yaml
```

Result example:

```
#                                                              out-of-place                       in-place
#       size         count      type   redop    root     time   algbw   busbw #wrong     time   algbw   busbw #wrong
#        (B)    (elements)                               (us)  (GB/s)  (GB/s)            (us)  (GB/s)  (GB/s)
   536870912     134217728     float     sum      -1   2378.7  225.70  423.19      0   2358.1  227.68  426.89      0
  1073741824     268435456     float     sum      -1   4464.5  240.50  450.95      0   4461.2  240.69  451.29      0
  2147483648     536870912     float     sum      -1   8697.7  246.90  462.94      0   8699.8  246.84  462.83      0
  4294967296    1073741824     float     sum      -1    17406  246.75  462.66      0    17185  249.93  468.62      0
  8589934592    2147483648     float     sum      -1    33782  254.28  476.77      0    33732  254.65  477.48      0
# Out of bounds values : 0 OK
# Avg bus bandwidth    : 456.361
```

3. InfiniBand Direct Test:

```bash
sky launch -c infiniband infiniband.yaml
```

Result example:

```
---------------------------------------------------------------------------------------
                    Send BW Test
 Dual-port       : OFF          Device         : mlx5_0
 Number of qps   : 1            Transport type : IB
 Connection type : RC           Using SRQ      : OFF
 PCIe relax order: ON
 ibv_wr* API     : ON
 TX depth        : 128
 CQ Moderation   : 1
 Mtu             : 4096[B]
 Link type       : IB
 Max inline data : 0[B]
 rdma_cm QPs     : OFF
 Data ex. method : Ethernet
---------------------------------------------------------------------------------------
 local address: LID 0x4624 QPN 0x0127 PSN 0x45bd8e
 remote address: LID 0x461b QPN 0x0127 PSN 0x1d3746
---------------------------------------------------------------------------------------
 #bytes     #iterations    BW peak[Gb/sec]    BW average[Gb/sec]   MsgRate[Mpps]
 65536      1000             357.12             353.53             0.674308
---------------------------------------------------------------------------------------
```

## Additional Resources

The Nebius team maintains a comprehensive collection of example configurations in their [ml-cookbook repository](https://github.com/nebius/ml-cookbook/tree/main/skypilot). These examples cover various use cases and can help you get started with different ML workloads on Nebius using SkyPilot.

## Appendix: creating a Nebius Kubernetes cluster with InfiniBand enabled

To enable infiniband for a Nebius Kubernetes cluster, you need to create a GPU node group with InfiniBand enabled, for more details, refer to the [Nebius documentation](https://docs.nebius.com/kubernetes/gpu/clusters#enable).


1. Create a managed service for Kubernetes cluster or bring in your own Kubernetes cluster.

Create a Nebius Kubernetes cluster:

```bash
export PROJECT_ID=your-project-id
export NB_SUBNET_ID=$(nebius vpc subnet list \
  --parent-id $PROJECT_ID \
  --format json \
  | jq -r '.items[0].metadata.id')

export NB_K8S_CLUSTER_ID=$(nebius mk8s cluster create \
  --name infini \
  --control-plane-version 1.30 \
  --control-plane-subnet-id $NB_SUBNET_ID \
  --control-plane-endpoints-public-endpoint=true \
  --parent-id=$PROJECT_ID \
  --format json | jq -r '.metadata.id')
```

<details>
<summary>Or, Bring in your own Kubernetes cluster</summary>

Find your Kubernetes cluster ID on the console or using the following command:

```bash
export PROJECT_ID=your-project-id
# Use the first cluster in the list
export NB_K8S_CLUSTER_ID=$(nebius mk8s cluster list \
  --parent-id $PROJECT_ID \
  --format json \
  | jq -r '.items[0].metadata.id')
```

</details>


2. To enable InfiniBand for a node group, you need to create a GPU cluster first, then specify the GPU cluster when creating the node group.

```bash
export INFINIBAND_FABRIC=fabric-3
export NB_GPU_CLUSTER_ID=$(nebius compute gpu-cluster create \
  --name gpu-cluster-name \
  --infiniband-fabric $INFINIBAND_FABRIC \
  --parent-id $PROJECT_ID \
  --format json \
  | jq -r ".metadata.id")

nebius mk8s node-group create \
  --parent-id $NB_K8S_CLUSTER_ID \
  --name infini-ib-group \
  --fixed-node-count 2 \
  --template-resources-platform gpu-h100-sxm \
  --template-resources-preset 8gpu-128vcpu-1600gb \
  --template-gpu-cluster-id $NB_GPU_CLUSTER_ID \
  --template-gpu-settings-drivers-preset cuda12
```

Refer to the [Nebius documentation](https://docs.nebius.com/compute/clusters/gpu#fabrics) for how to select the fabric according to the type of GPUs you are going to use.

3. Setup Kubeconfig and setup Nvidia GPUs 

```bash
nebius mk8s cluster get-credentials --id $NB_K8S_CLUSTER_ID --external
sky check k8s
```

> Note: To create a node group with a GPU cluster, you need to specify a compatible preset (number of GPUs and vCPUs, RAM size). The compatible platforms and presets are as below:
> 
| Platform                                                      | Presets             | Regions                          |
| ------------------------------------------------------------- | ------------------- | -------------------------------- |
| NVIDIA® H100 NVLink with Intel Sapphire Rapids (gpu-h100-sxm) | 8gpu-128vcpu-1600gb | eu-north1                        |
| NVIDIA® H200 NVLink with Intel Sapphire Rapids (gpu-h200-sxm) | 8gpu-128vcpu-1600gb | eu-north1, eu-west1, us-central1 |

Now you have a Kubernetes cluster that have the GPUs interconnected using InfiniBand.
