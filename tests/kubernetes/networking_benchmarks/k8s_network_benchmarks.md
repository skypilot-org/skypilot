# Kubernetes Networking Benchmarking

A SkyPilot pod in Kubernetes can be accessed via three methods:
1. `direct`: NodePort service directly exposing the pod's SSH port
2. `sshjump` (DEPRECATED): NodePort service exposing a SSH jump pod that connects to the SkyPilot pod
3. `port-forward`: Uses `kubectl port-forward` to connect to ClusterIP service pointing to a SSH jump pod that connects to the SkyPilot pod

`direct` requires opening a large range of ports on the cluster's firewall.
`sshjump` requires opening only one port on the cluster's firewall, but requires an additional SSH connection to the jump pod.
`port-forward` does not require opening any ports on the cluster's firewall, but routes all traffic over the kubernetes control plane.

This document benchmarks the three approaches on a Kind cluster and a GKE cluster.

We run two kinds of benchmarks:
1. `sky launch` benchmarks: how long does it take to launch a SkyPilot pod
2. Rsync benchmarks: how long does it take to copy a directory containing 1000 1MB files to the SkyPilot pod

In summary, we find that `direct` is only marginally faster (~10%) than `sshjump` and `port-forward` for both `sky launch` and rsync benchmarks.

Given these results, this document recommends using `port-forward` for all SkyPilot deployments because of its significant ease of use and security benefits.

## Benchmark environment
These benchmarks were run on a 2023 M2 Max Macbook Pro with 32GB of RAM. Each benchmark was run on a GKE cluster and a local kind cluster (`sky local up`). Kubernetes v1.27 was used. This is on a 100mbit home connection.

Note that GKE benchmarks, particularly rsync, are sensitive the network connection between the benchmarking machine and the GKE cluster.

# `sky launch` benchmarks

Runs 5 sky launch times and reports the average of the last four runs.

Usage:
```
./skylaunch_bench.sh <output_suffix>
# e.g., `./skylaunch_bench.sh gkedirect` will create a file called skylaunch_results_gkedirect.txt
```

|             | Direct  | SSHJump | port-forward |
|-------------|---------|---------|--------------|
| **GKE**     | 64.51s  | 62.51s  | 69.75s       |
| **Kind**    | 26.65s  | 28.37s  | 28.75s       |

## Rsync benchmarks

Creates a directory with 1000 1MB files and copies it to the SkyPilot pod. Runs 5 rsync times and reports the average of the last four runs.

Usage:
```
./rsync_bench.sh <output_suffix>
# e.g., `./rsync_bench.sh gkedirect` will create a file called rsync_results_gkedirect.txt
```

|             | Direct  | SSHJump | port-forward |
|-------------|---------|---------|--------------|
| **GKE**     | 337.49s | 347.49s | 361.49s      |
| **Kind**    | 31.49s  | 31.71s  | 33.21s       |
