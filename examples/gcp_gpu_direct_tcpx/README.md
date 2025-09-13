# Using High-Performance GPU Networking on GCP and GKE with SkyPilot

SkyPilot supports advanced GPU networking technologies on both GCP VMs and GKE clusters, enabling high-performance inter-GPU communication for distributed deep learning and HPC workloads. This includes support for:

- **GPUDirect-TCPX**: a3-highgpu-8g, a3-edgegpu-8g (H100)
- **GPUDirect-RDMA**: a3-ultragpu-8g (H200), a4-highgpu-8g (B200)

## NCCL Test Example YAMLs

We offer example YAMLs for running NCCL tests to verify high-performance GPU networking on GCP, covering both VM-based and GKE-based deployments:

| Configuration | Target Platform | GPU Networking Technology | VM Types |
|---------------|----------------|---------------------------|----------|
| [`nccl_tcpx_gcpvm_h100.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/gcp_gpu_direct_tcpx/nccl_tcpx_gcpvm_h100.yaml) | GCP VM | GPUDirect-TCPX | a3-highgpu-8g, a3-edgegpu-8g |
| [`nccl_tcpx_gke_h100.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/gcp_gpu_direct_tcpx/nccl_tcpx_gke.yaml) | GKE | GPUDirect-TCPX | a3-highgpu-8g, a3-edgegpu-8g |
| [`nccl_rdma_gke_h200.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/gcp_gpu_direct_tcpx/nccl_rdma_gke_h200.yaml) | GKE | GPUDirect-RDMA | a3-ultragpu-8g |


## GKE: Using High-Performance GPU Networking on GKE

SkyPilot supports advanced GPU networking GKE clusters, including GPUDirect-TCPX and, GPUDirect-RDMA by simply setting `network_tier: best`:

```
resources:
  ...
  network_tier: best # Turn on GPUDirect if available 
```

To make sure your cluster is set up correctly, refer to the [GKE documentation](https://cloud.google.com/kubernetes-engine/docs/how-to/gpu-bandwidth-gpudirect-tcpx#create-vpcs-subnets) to setup the cluster with the appropriate networking configuration.

In addition to creating a node pool with fixed node size to request the desired GPU instances, you can also use Dynamic Workload Scheduler (DWS) on GKE to provision the nodes, refer to [using DWS on GKE](https://docs.skypilot.co/en/latest/reservations/reservations.html#using-dws-on-gke) for more details.

After setting up the GKE cluster, you can run the appropriate NCCL tests for your GPUs:
* H100: [`sky launch -c nccl nccl_tcpx_gke_h100.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/gcp_gpu_direct_tcpx/nccl_tcpx_gke.yaml)
* H200: [`sky launch -c nccl nccl_rdma_gke_h200.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/gcp_gpu_direct_tcpx/nccl_rdma_gke_h200.yaml)

### GPUDirect-RDMA on A3-ultragpu-8g (H200)

We validated GPUDirect-RDMA performance on `a3-ultragpu-8g` instances with H200 GPUs. Testing was conducted on a 2-node cluster with 16x H200 GPUs (8 per node) using the configuration in [`nccl_rdma_gke_h200.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/gcp_gpu_direct_tcpx/nccl_rdma_gke_h200.yaml).

The scaling curves and bandwidth measurements match exactly with Google's official benchmarks shown in their [documentation](https://cloud.google.com/ai-hypercomputer/docs/create/gke-ai-hypercompute-custom#flex-start).

### Running [`nccl_rdma_gke_h200.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/gcp_gpu_direct_tcpx/nccl_rdma_gke_h200.yaml) on SkyPilot

```console
$ sky launch -c nccl nccl_rdma_gke_h200.yaml
...
(head, rank=0, pid=3808) All nodes: 10.100.9.12:8,10.100.10.12:8
(worker1, rank=1, pid=2769, ip=10.100.10.12) Worker nodes
(head, rank=0, pid=3808) [1,0]<stdout>:# nThread 1 nGpus 1 minBytes 1024 maxBytes 8589934592 step: 2(factor) warmup iters: 5 iters: 100 agg iters: 1 validation: 1 graph: 0
(head, rank=0, pid=3808) [1,0]<stdout>:#
(head, rank=0, pid=3808) [1,0]<stdout>:# Using devices
(head, rank=0, pid=3808) [1,0]<stdout>:#  Rank  0 Group  0 Pid   7774 on nc-d87e1263-head device  0 [0000:8f:00] NVIDIA H200
(head, rank=0, pid=3808) [1,0]<stdout>:#  Rank  1 Group  0 Pid   7775 on nc-d87e1263-head device  1 [0000:90:00] NVIDIA H200
(head, rank=0, pid=3808) [1,0]<stdout>:#  Rank  2 Group  0 Pid   7776 on nc-d87e1263-head device  2 [0000:96:00] NVIDIA H200
(head, rank=0, pid=3808) [1,0]<stdout>:#  Rank  3 Group  0 Pid   7778 on nc-d87e1263-head device  3 [0000:97:00] NVIDIA H200
(head, rank=0, pid=3808) [1,0]<stdout>:#  Rank  4 Group  0 Pid   7783 on nc-d87e1263-head device  4 [0000:c4:00] NVIDIA H200
(head, rank=0, pid=3808) [1,0]<stdout>:#  Rank  5 Group  0 Pid   7786 on nc-d87e1263-head device  5 [0000:c5:00] NVIDIA H200
(head, rank=0, pid=3808) [1,0]<stdout>:#  Rank  6 Group  0 Pid   7789 on nc-d87e1263-head device  6 [0000:cb:00] NVIDIA H200
(head, rank=0, pid=3808) [1,0]<stdout>:#  Rank  7 Group  0 Pid   7792 on nc-d87e1263-head device  7 [0000:cc:00] NVIDIA H200
(head, rank=0, pid=3808) [1,0]<stdout>:#  Rank  8 Group  0 Pid   4926 on nc-d87e1263-worker1 device  0 [0000:8f:00] NVIDIA H200
(head, rank=0, pid=3808) [1,0]<stdout>:#  Rank  9 Group  0 Pid   4927 on nc-d87e1263-worker1 device  1 [0000:90:00] NVIDIA H200
(head, rank=0, pid=3808) [1,0]<stdout>:#  Rank 10 Group  0 Pid   4928 on nc-d87e1263-worker1 device  2 [0000:96:00] NVIDIA H200
(head, rank=0, pid=3808) [1,0]<stdout>:#  Rank 11 Group  0 Pid   4929 on nc-d87e1263-worker1 device  3 [0000:97:00] NVIDIA H200
(head, rank=0, pid=3808) [1,0]<stdout>:#  Rank 12 Group  0 Pid   4931 on nc-d87e1263-worker1 device  4 [0000:c4:00] NVIDIA H200
(head, rank=0, pid=3808) [1,0]<stdout>:#  Rank 13 Group  0 Pid   4934 on nc-d87e1263-worker1 device  5 [0000:c5:00] NVIDIA H200
(head, rank=0, pid=3808) [1,0]<stdout>:#  Rank 14 Group  0 Pid   4937 on nc-d87e1263-worker1 device  6 [0000:cb:00] NVIDIA H200
(head, rank=0, pid=3808) [1,0]<stdout>:#  Rank 15 Group  0 Pid   4940 on nc-d87e1263-worker1 device  7 [0000:cc:00] NVIDIA H200
(head, rank=0, pid=3808) [1,0]<stdout>:#
(head, rank=0, pid=3808) [1,0]<stdout>:#                                                              out-of-place                       in-place
(head, rank=0, pid=3808) [1,0]<stdout>:#       size         count      type   redop    root     time   algbw   busbw #wrong     time   algbw   busbw #wrong
(head, rank=0, pid=3808) [1,0]<stdout>:#        (B)    (elements)                               (us)  (GB/s)  (GB/s)            (us)  (GB/s)  (GB/s)
(head, rank=0, pid=3808) [1,0]<stdout>:        1024            16     float    none      -1    28.39    0.04    0.03      0    28.07    0.04    0.03      0
(head, rank=0, pid=3808) [1,0]<stdout>:        2048            32     float    none      -1    28.29    0.07    0.07      0    28.24    0.07    0.07      0
(head, rank=0, pid=3808) [1,0]<stdout>:        4096            64     float    none      -1    28.85    0.14    0.13      0    28.64    0.14    0.13      0
(head, rank=0, pid=3808) [1,0]<stdout>:        8192           128     float    none      -1    34.12    0.24    0.23      0    32.47    0.25    0.24      0
(head, rank=0, pid=3808) [1,0]<stdout>:       16384           256     float    none      -1    33.28    0.49    0.46      0    33.29    0.49    0.46      0
(head, rank=0, pid=3808) [1,0]<stdout>:       32768           512     float    none      -1    34.80    0.94    0.88      0    34.86    0.94    0.88      0
(head, rank=0, pid=3808) [1,0]<stdout>:       65536          1024     float    none      -1    40.62    1.61    1.51      0    41.22    1.59    1.49      0
(head, rank=0, pid=3808) [1,0]<stdout>:      131072          2048     float    none      -1    36.17    3.62    3.40      0    40.87    3.21    3.01      0
(head, rank=0, pid=3808) [1,0]<stdout>:      262144          4096     float    none      -1    41.81    6.27    5.88      0    37.99    6.90    6.47      0
(head, rank=0, pid=3808) [1,0]<stdout>:      524288          8192     float    none      -1    43.98   11.92   11.18      0    45.96   11.41   10.69      0
(head, rank=0, pid=3808) [1,0]<stdout>:     1048576         16384     float    none      -1    58.46   17.94   16.82      0    54.81   19.13   17.93      0
(head, rank=0, pid=3808) [1,0]<stdout>:     2097152         32768     float    none      -1    68.40   30.66   28.74      0    70.87   29.59   27.74      0
(head, rank=0, pid=3808) [1,0]<stdout>:     4194304         65536     float    none      -1    76.56   54.78   51.36      0    76.13   55.09   51.65      0
(head, rank=0, pid=3808) [1,0]<stdout>:     8388608        131072     float    none      -1    86.92   96.51   90.47      0    85.77   97.81   91.70      0
(head, rank=0, pid=3808) [1,0]<stdout>:    16777216        262144     float    none      -1    116.2  144.43  135.41      0    114.9  146.00  136.87      0
(head, rank=0, pid=3808) [1,0]<stdout>:    33554432        524288     float    none      -1    174.4  192.45  180.42      0    172.4  194.66  182.49      0
(head, rank=0, pid=3808) [1,0]<stdout>:    67108864       1048576     float    none      -1    278.1  241.27  226.19      0    270.5  248.10  232.59      0
(head, rank=0, pid=3808) [1,0]<stdout>:   134217728       2097152     float    none      -1    499.6  268.67  251.88      0    483.8  277.44  260.10      0
(head, rank=0, pid=3808) [1,0]<stdout>:   268435456       4194304     float    none      -1    885.5  303.16  284.21      0    870.7  308.30  289.03      0
(head, rank=0, pid=3808) [1,0]<stdout>:   536870912       8388608     float    none      -1   1575.6  340.75  319.45      0   1568.7  342.24  320.85      0
(head, rank=0, pid=3808) [1,0]<stdout>:  1073741824      16777216     float    none      -1   3123.9  343.72  322.23      0   3079.7  348.65  326.86      0
(head, rank=0, pid=3808) [1,0]<stdout>:  2147483648      33554432     float    none      -1   6229.6  344.72  323.18      0   6107.6  351.61  329.63      0
(head, rank=0, pid=3808) [1,0]<stdout>:  4294967296      67108864     float    none      -1    12416  345.92  324.30      0    12133  354.00  331.87      0
(head, rank=0, pid=3808) [1,0]<stdout>:  8589934592     134217728     float    none      -1    24724  347.44  325.72      0    24214  354.75  332.58      0
(head, rank=0, pid=3808) [1,0]<stdout>:# Out of bounds values : 0 OK
(head, rank=0, pid=3808) [1,0]<stdout>:# Avg bus bandwidth    : 122.073
(head, rank=0, pid=3808) [1,0]<stdout>:#
```

### Comparing with raw NCCL test pods from GCP documentation

```console
$ kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/refs/heads/master/gpudirect-rdma/nccl-test-a4.yaml
...
$ kubectl exec nccl-test-host-1 -it -- /usr/local/gib/scripts/run_nccl_tests.sh -t all_gather -b 1K -e 8G nccl-host-1 nccl-host-2
...
# nThread 1 nGpus 1 minBytes 1024 maxBytes 8589934592 step: 2(factor) warmup iters: 50 iters: 100 agg iters: 1 validation: 1 graph: 0
#
# Using devices
#  Rank  0 Group  0 Pid  13581 on nccl-test-host-1 device  0 [0000:8f:00] NVIDIA H200
#  Rank  1 Group  0 Pid  13779 on nccl-test-host-1 device  1 [0000:90:00] NVIDIA H200
#  Rank  2 Group  0 Pid  13629 on nccl-test-host-1 device  2 [0000:96:00] NVIDIA H200
#  Rank  3 Group  0 Pid  13795 on nccl-test-host-1 device  3 [0000:97:00] NVIDIA H200
#  Rank  4 Group  0 Pid  13790 on nccl-test-host-1 device  4 [0000:c4:00] NVIDIA H200
#  Rank  5 Group  0 Pid  13750 on nccl-test-host-1 device  5 [0000:c5:00] NVIDIA H200
#  Rank  6 Group  0 Pid  13751 on nccl-test-host-1 device  6 [0000:cb:00] NVIDIA H200
#  Rank  7 Group  0 Pid  13754 on nccl-test-host-1 device  7 [0000:cc:00] NVIDIA H200
#  Rank  8 Group  0 Pid  13708 on nccl-test-host-2 device  0 [0000:8f:00] NVIDIA H200
#  Rank  9 Group  0 Pid  13749 on nccl-test-host-2 device  1 [0000:90:00] NVIDIA H200
#  Rank 10 Group  0 Pid  13728 on nccl-test-host-2 device  2 [0000:96:00] NVIDIA H200
#  Rank 11 Group  0 Pid  13735 on nccl-test-host-2 device  3 [0000:97:00] NVIDIA H200
#  Rank 12 Group  0 Pid  13648 on nccl-test-host-2 device  4 [0000:c4:00] NVIDIA H200
#  Rank 13 Group  0 Pid  13685 on nccl-test-host-2 device  5 [0000:c5:00] NVIDIA H200
#  Rank 14 Group  0 Pid  13653 on nccl-test-host-2 device  6 [0000:cb:00] NVIDIA H200
#  Rank 15 Group  0 Pid  13751 on nccl-test-host-2 device  7 [0000:cc:00] NVIDIA H200
NCCL version 2.26.6+cuda12.8
#
#                                                              out-of-place                       in-place
#       size         count      type   redop    root     time   algbw   busbw #wrong     time   algbw   busbw #wrong
#        (B)    (elements)                               (us)  (GB/s)  (GB/s)            (us)  (GB/s)  (GB/s)
        1024            16     float    none      -1    28.70    0.04    0.03      0    27.82    0.04    0.03      0
        2048            32     float    none      -1    28.05    0.07    0.07      0    28.03    0.07    0.07      0
        4096            64     float    none      -1    28.39    0.14    0.14      0    28.35    0.14    0.14      0
        8192           128     float    none      -1    31.44    0.26    0.24      0    31.23    0.26    0.25      0
       16384           256     float    none      -1    31.61    0.52    0.49      0    31.70    0.52    0.48      0
       32768           512     float    none      -1    32.81    1.00    0.94      0    32.76    1.00    0.94      0
       65536          1024     float    none      -1    34.77    1.88    1.77      0    34.58    1.90    1.78      0
      131072          2048     float    none      -1    34.65    3.78    3.55      0    37.37    3.51    3.29      0
      262144          4096     float    none      -1    39.47    6.64    6.23      0    37.89    6.92    6.49      0
      524288          8192     float    none      -1    42.75   12.26   11.50      0    40.59   12.92   12.11      0
     1048576         16384     float    none      -1    54.70   19.17   17.97      0    53.14   19.73   18.50      0
     2097152         32768     float    none      -1    69.52   30.16   28.28      0    66.52   31.52   29.55      0
     4194304         65536     float    none      -1    77.41   54.18   50.79      0    71.70   58.50   54.84      0
     8388608        131072     float    none      -1    87.98   95.35   89.39      0    86.18   97.34   91.25      0
    16777216        262144     float    none      -1    117.6  142.68  133.76      0    123.6  135.69  127.21      0
    33554432        524288     float    none      -1    177.2  189.36  177.53      0    176.9  189.66  177.81      0
    67108864       1048576     float    none      -1    277.8  241.56  226.47      0    271.8  246.94  231.50      0
   134217728       2097152     float    none      -1    493.7  271.86  254.87      0    486.3  276.01  258.76      0
   268435456       4194304     float    none      -1    876.1  306.38  287.23      0    870.5  308.38  289.11      0
   536870912       8388608     float    none      -1   1580.2  339.74  318.51      0   1568.3  342.32  320.93      0
  1073741824      16777216     float    none      -1   3126.8  343.40  321.93      0   3084.1  348.16  326.40      0
  2147483648      33554432     float    none      -1   6218.0  345.36  323.78      0   6097.1  352.22  330.20      0
  4294967296      67108864     float    none      -1    12400  346.38  324.73      0    12135  353.94  331.82      0
  8589934592     134217728     float    none      -1    24739  347.22  325.52      0    24244  354.31  332.16      0
# Out of bounds values : 0 OK
# Avg bus bandwidth    : 121.902
#
```

## GCP VMs: GPUDirect-TCPX on A3 VMs (H100)

> **Note:** The following instructions apply only to GCP VMs (a3-highgpu-8g and a3-edgegpu-8g). For GKE clusters, see the [GKE: Using High-Performance GPU Networking on GKE](#gke-using-high-performance-gpu-networking-on-gke) above.


This example demonstrates how to run [NCCL tests](https://github.com/NVIDIA/nccl-tests) on a GCP cluster with A3 VMs, comparing performance with and without GPUDirect-TCPX enabled.

Enable GPUDirect-TCPX on GCP clusters with `a3-highgpu-8g` or `a3-edgegpu-8g` VMs by adding a single configuration parameter to your SkyPilot YAML:

```yaml
config:
  gcp:
    enable_gpu_direct: true
```

With `enable_gpu_direct: true`, SkyPilot automatically:

1. Creates a dedicated network infrastructure:
   - 1 management VPC
   - 4 data VPCs
   - Corresponding subnets for each VPC

2. Provisions VMs with GPUDirect-TCPX support:
   - Launches VMs with the specified instance type
   - Uses GPUDirect-TCPX-compatible images
   - Installs necessary GPU drivers
   - Deploys the GPUDirect-TCPX Receive Data Path Manager service
   - Configures NVIDIA Collective Communications Library (NCCL) and GPUDirect-TCPX plugin

### Running NCCL Tests with GPUDirect-TCPX

The complete configuration is available in [`nccl_tcpx_gcpvm_h100.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/gcp_gpu_direct_tcpx/nccl_tcpx_gcpvm_h100.yaml). The configuration includes:
- `image_id`: Pre-configured environment for NCCL testing
- `instance_type`: Set to `a3-highgpu-8g`

To run the NCCL test with GPUDirect-TCPX:

```bash
sky launch -c tcpx nccl_tcpx_gcpvm_h100.yaml
```

SkyPilot will:
1. Deploy a GCP cluster with GPUDirect-TCPX enabled nodes
2. Execute NCCL performance tests
3. Output detailed performance metrics

Successful GPUDirect-TCPX activation is confirmed by these log entries:

```
NCCL INFO NET/GPUDirectTCPX ver. 3.1.8.
NCCL INFO NET/GPUDirectTCPX : GPUDirectTCPX enable: 1
```

> **Note:** To run tests without GPUDirect-TCPX, use:
> ```bash
> sky launch -c tcpx --env USE_GPU_DIRECT=false nccl_tcpx_gcpvm_h100.yaml
> ```

### Performance Benchmark Results

We conducted performance comparisons using NCCL tests on a GCP cluster with 2x a3-highgpu-8g (2xH100:8) instances. The speed-up is calculated as:
```
Speed-up = busbw GPUDirect-TCPX (GB/s) / busbw Non-GPUDirect-TCPX (GB/s)
```

| Message Size | busbw GPUDirect-TCPX (GB/s) | busbw Non-GPUDirect-TCPX (GB/s) | Speed-up |
|--------------|------------|-------------|--------------|
| 8 B          | 0          | 0           | -            |
| 16 B         | 0          | 0           | -            |
| 32 B         | 0          | 0           | -            |
| 64 B         | 0          | 0           | -            |
| 128 B        | 0          | 0           | -            |
| 256 B        | 0          | 0           | -            |
| 512 B        | 0          | 0           | -            |
| 1 KB         | 0.01       | 0.01        | 1 x          |
| 2 KB         | 0.01       | 0.01        | 1 x          |
| 4 KB         | 0.01       | 0.02        | 0.5 x        |
| 8 KB         | 0.02       | 0.04        | 0.5 x        |
| 16 KB        | 0.04       | 0.09        | 0.4 x        |
| 32 KB        | 0.09       | 0.12        | 0.7 x        |
| 64 KB        | 0.11       | 0.17        | 0.6 x        |
| 128 KB       | 0.19       | 0.15        | 1.2 x        |
| 256 KB       | 0.35       | 0.23        | 1.5 x        |
| 512 KB       | 0.65       | 0.47        | 1.4 x        |
| 1 MB         | 1.33       | 0.95        | 1.4 x        |
| 2 MB         | 2.43       | 1.87        | 1.3 x        |
| 4 MB         | 4.8        | 3.64        | 1.3 x        |
| 8 MB         | 9.21       | 7.1         | 1.3 x        |
| 16 MB        | 17.16      | 8.83        | 1.9 x        |
| 32 MB        | 30.08      | 12.07       | 2.5 x        |
| 64 MB        | 45.31      | 12.48       | 3.6 x        |
| 128 MB       | 61.58      | 16.27       | 3.8 x        |
| 256 MB       | 67.82      | 20.93       | 3.2 x        |
| 512 MB       | 67.09      | 19.93       | 3.3 x        |
| 1 GB         | 66.2       | 20.09       | 3.3 x        |
| 2 GB         | 65.72      | 19.39       | 3.4 x        |

### Key Performance Insights

| Message Size Range | Performance Characteristics |
|-------------------|----------------------------|
| ≤ 128 KB          | Minimal benefit - GPUDirect-TCPX may introduce slight overhead for small messages, with comparable or lower bandwidth than non-GPUDirect mode |
| 256 KB – 8 MB     | Moderate improvement - Speedup of 1.5–1.9×, with performance crossover point at 128–256 KB |
| ≥ 16 MB           | Significant advantage - 2.5–3.8× speedup, with GPUDirect-TCPX maintaining 65–67 GB/s versus ~20 GB/s without it |

GPUDirect-TCPX's direct GPU-to-NIC communication path eliminates CPU and system memory bottlenecks, delivering superior throughput for large-scale data transfers. This makes it particularly effective for distributed deep learning workloads and high-performance computing applications.
