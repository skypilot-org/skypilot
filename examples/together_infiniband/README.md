# Using InfiniBand in Together AI with SkyPilot

SkyPilot provides the `network_tier: best` configuration option that automatically enables InfiniBand support on Together AI Kubernetes clusters. This eliminates the need for manual configuration of security contexts and environment variables.

## InfiniBand on Together AI Kubernetes clusters

Simply add ``network_tier: best`` to your resources specification:

```yaml
resources:
  infra: k8s
  accelerators: H100:8
  network_tier: best
```

This enables the InfiniBand for inter-GPU communication, and SkyPilot will automatically setup the environment variables for you.

## Running NCCL test using SkyPilot

Check the [`nccl_network_tier.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/together_infiniband/nccl_network_tier.yaml) for the complete SkyPilot cluster yaml configurations.

The `image_id` provides the environment setup for [NCCL](https://developer.nvidia.com/nccl) (NVIDIA Collective Communications Library).

To run the NCCL test with InfiniBand support:

```bash
sky launch -c infiniband nccl_network_tier.yaml
```

SkyPilot will:
1. Schedule the job on the Kubernetes cluster with required GPU nodes
2. Launch Pods and execute the NCCL performance test
3. Output performance metrics showing the benefits of InfiniBand for distributed training

The example result is as below:

```
#                                                              out-of-place                       in-place
#       size         count      type   redop    root     time   algbw   busbw #wrong     time   algbw   busbw #wrong
#        (B)    (elements)                               (us)  (GB/s)  (GB/s)            (us)  (GB/s)  (GB/s)
   536870912     134217728     float     sum      -1   2407.5  222.99  418.12      0   2380.3  225.55  422.90      0
  1073741824     268435456     float     sum      -1   4524.3  237.33  444.99      0   4531.6  236.95  444.28      0
  2147483648     536870912     float     sum      -1   8787.5  244.38  458.21      0   8780.7  244.57  458.56      0
  4294967296    1073741824     float     sum      -1    17327  247.88  464.77      0    17328  247.86  464.74      0
  8589934592    2147483648     float     sum      -1    34462  249.26  467.36      0    34482  249.11  467.08      0
# Out of bounds values : 0 OK
# Avg bus bandwidth    : 451.101
```

> **NOTE:** To run NCCL tests without InfiniBand, you can launch a cluster with `nccl_no_ib.yaml`:
>
> ```bash
> sky launch -c no_infiniband nccl_no_ib.yaml
> ```
