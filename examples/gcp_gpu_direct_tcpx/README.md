# Using GCP GPUDirect-TCPX on A3 VM with SkyPilot

[GPUDirect-TCPX](https://cloud.google.com/compute/docs/gpus/gpudirect) is a high-performance networking technology that enables direct communication between GPUs and network interfaces. By bypassing the CPU and system memory, it significantly enhances network performance for A3 VMs, particularly for large data transfers.

When deploying `a3-highgpu-8g` or `a3-edgegpu-8g` VMs, combining GPUDirect-TCPX with Google Virtual NIC (gVNIC) delivers optimal network performance with minimal latency between applications and the network infrastructure.

This example demonstrates how to run [NCCL tests](https://github.com/NVIDIA/nccl-tests) on a GCP cluster with `a3-highgpu-8g` VMs, comparing performance with and without GPUDirect-TCPX enabled.

## TL;DR: enable GPUDirect-TCPX with SkyPilot

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

## Running NCCL Tests with GPUDirect-TCPX

The complete configuration is available in [`gpu_direct_tcpx.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/gcp_gpu_direct_tcpx/gpu_direct_tcpx.yaml). The configuration includes:
- `image_id`: Pre-configured environment for NCCL testing
- `instance_type`: Set to `a3-highgpu-8g`

To run the NCCL test with GPUDirect-TCPX:

```bash
sky launch -c tcpx gpu_direct_tcpx.yaml
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
> sky launch -c tcpx --env USE_GPU_DIRECT=false gpu_direct_tcpx.yaml
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
