# Using InfiniBand in CoreWeave with SkyPilot

## Setup InfiniBand with a single SkyPilot configuration

SkyPilot provides the `network_tier: best` configuration option that automatically enables InfiniBand support on CoreWeave Kubernetes clusters. This eliminates the need for manual configuration of `rdma/ib` resources, security contexts and environment variables.

### InfiniBand on CoreWeave managed Kubernetes clusters

Simply add ``network_tier: best`` to your resources specification:

```yaml
resources:
  infra: k8s
  accelerators: H200:8
  network_tier: best
```

### End-to-end Example

Check the [`coreweave_nccl_test.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/coreweave_infiniband/coreweave_nccl_test.yaml) for a complete example using the simplified configuration:

```bash
sky launch -c nccl coreweave_nccl_test.yaml
```

This enables the InfiniBand for inter-GPU communication and SkyPilot will automatically setup the environment variables for you.

SkyPilot will:
1. Schedule the job on a Kubernetes cluster with required GPU nodes
2. Launch Pods and execute the NCCL performance test
3. Output performance metrics showing the benefits of InfiniBand for distributed training

NCCL test on 16 H200s (2 nodes with H200:8 each) with infiniband:

```
#                                                              out-of-place                       in-place
#     size         count      type   redop    root     time   algbw   busbw #wrong     time   algbw   busbw #wrong
#      (B)    (elements)                               (us)  (GB/s)  (GB/s)            (us)  (GB/s)  (GB/s)
 536870912     134217728     float     sum      -1   2387.7  224.85  421.59      0   2325.5  230.86  432.86      0
1073741824     268435456     float     sum      -1   4416.5  243.12  455.85      0   4425.1  242.65  454.96      0
2147483648     536870912     float     sum      -1   8562.4  250.80  470.26      0   8581.4  250.25  469.22      0
4294967296    1073741824     float     sum      -1    16852  254.86  477.86      0    16844  254.98  478.09      0
8589934592    2147483648     float     sum      -1    33460  256.72  481.35      0    33381  257.33  482.49      0

Avg bus bandwidth    : 462.453
```

> **NOTE:** To run NCCL tests without InfiniBand, you can comment out the `network_tier: best` line in the YAML file. This will use the default network configuration without InfiniBand.

NCCL test on 16 H200s (2 nodes with H200:8 each) WITHOUT infiniband:
```
#                                                              out-of-place                       in-place
#     size         count      type   redop    root     time   algbw   busbw #wrong     time   algbw   busbw #wrong
#      (B)    (elements)                               (us)  (GB/s)  (GB/s)            (us)  (GB/s)  (GB/s)
536870912      134217728     float     sum      -1   269865    1.99    3.73      0   267004    2.01    3.77      0
1073741824     268435456     float     sum      -1   536466    2.00    3.75      0   539227    1.99    3.73      0
2147483648     536870912     float     sum      -1  1071841    2.00    3.76      0  1078080    1.99    3.73      0
4294967296    1073741824     float     sum      -1  2137076    2.01    3.77      0  2143324    2.00    3.76      0
8589934592    2147483648     float     sum      -1  4280791    2.01    3.76      0  4339219    1.98    3.71      0

Avg bus bandwidth    : 3.7478
```