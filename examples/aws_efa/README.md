# Using Elastic Fabric Adapter (EFA) on AWS with SkyPilot

Elastic Fabric Adapter (EFA) is an AWS alternative to Nvidia infiniband that enables high levels of inter-node communications. It is specifically useful for distributed AI training and inference, which requires high network bandwidth across nodes.

## Using EFA on HyperPod/EKS with SkyPilot

### TL;DR: enable EFA with SkyPilot

You can enable EFA on AWS HyperPod/EKS clusters with an simple additional setting in your SkyPilot YAML:

```yaml
config:
  kubernetes:
    pod_config:
      spec:
        containers:
        - resources:
            limits:
              vpc.amazonaws.com/efa: 4
            requests:
              vpc.amazonaws.com/efa: 4
```



### Enable EFA with HyperPod/EKS

* On HyperPod (backed by EKS), EFA is enabled by default, and you don't need to do anything.
* On EKS, you may need to enable EFA with the [official AWS documentation](https://docs.aws.amazon.com/eks/latest/userguide/node-efa.html).

To check if EFA is enabled, run:
```
kubectl get nodes "-o=custom-columns=NAME:.metadata.name,INSTANCETYPE:.metadata.labels.node\.kubernetes\.io/instance-type,GPU:.status.allocatable.nvidia\.com/gpu,EFA:.status.allocatable.vpc\.amazonaws\.com/efa"
```
You can expect a sample output like:
```
NAME                           INSTANCETYPE      GPU   EFA
hyperpod-i-0beea7c849d1dc614   ml.p4d.24xlarge   8     4
hyperpod-i-0da69b9076c7ff6a4   ml.p4d.24xlarge   8     4
...
```

### Access HyperPod and run distributed job with SkyPilot

To access HyperPod and run distributed job with SkyPilot, see the SkyPilot [HyperPod example](https://github.com/skypilot-org/skypilot/blob/master/examples/hyperpod-eks).

#### Adding EFA configurations in SkyPilot YAML

To enable EFA in SkyPilot YAML, you can specify the following section in the SkyPilot YAML:

```yaml
config:
  kubernetes:
    pod_config:
      spec:
        containers:
        - resources:
            limits:
              vpc.amazonaws.com/efa: 4
            requests:
              vpc.amazonaws.com/efa: 4
```

This section is important for EFA integration:

- `config.kubernetes.pod_config`: Provides Kubernetes-specific pod configuration
- `spec.containers[0].resources`: Defines resource requirements
  - `limits.vpc.amazonaws.com/efa: 4`: Limits the Pod to use 4 EFA devices
  - `requests.vpc.amazonaws.com/efa: 4`: Requests 4 EFA devices for the Pod


The `vpc.amazonaws.com/efa` resource type is exposed by the AWS EFA device plugin in Kubernetes. 
To see how many EFA are available for each instance types that have EFA, see the [Network cards](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-eni.html#network-cards) list in the Amazon EC2 User Guide.

Check the following table for the GPU and EFA count mapping for AWS instance types:

| Instance Type | GPU Type | #EFA |
|---------------|----------|------|
| p4d.24xlarge  | A100:8   | 4    |
| p4de.24xlarge | A100:8   | 4    |
| p5.48xlarge   | H100:8   | 32   |
| p5e.48xlarge  | H200:8   | 32   |
| p5en.48xlarge | H200:8   | 16   |
| g5.8xlarge    | A10G:1   | 1    |
| g5.12xlarge   | A10G:4   | 1    |
| g5.16xlarge   | A10G:1   | 1    |
| g5.24xlarge   | A10G:4   | 1    |
| g5.48xlarge   | A10G:8   | 1    |
| g4dn.8xlarge  | T4:1     | 1    |
| g4dn.12xlarge | T4:4     | 1    |
| g4dn.16xlarge | T4:1     | 1    |
| g4dn.metal    | T4:8     | 1    |
| g6.8xlarge    | L4:1     | 1    |
| g6.12xlarge   | L4:4     | 1    |
| g6.16xlarge   | L4:1     | 1    |
| g6.24xlarge   | L4:4     | 1    |
| g6.48xlarge   | L4:8     | 1    |
| g6e.8xlarge   | L40S:1   | 1    |
| g6e.12xlarge  | L40S:4   | 1    |
| g6e.16xlarge  | L40S:1   | 1    |
| g6e.24xlarge  | L40S:4   | 2    |
| g6e.48xlarge  | L40S:8   | 4    |


Update the EFA number in the [`nccl_efa.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/aws_efa/nccl_efa.yaml) for the GPUs you use.

### Running NCCL test with EFA using SkyPilot

Check the [`nccl_efa.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/aws_efa/nccl_efa.yaml) for the complete SkyPilot cluster yaml configurations.

The `image_id` provides the environment setup for [NCCL](https://developer.nvidia.com/nccl) (NVIDIA Collective Communications Library) and EFA (Elastic Fabric Adapter).

To run the NCCL test with EFA support:

```bash
sky launch -c efa nccl_efa.yaml
```

SkyPilot will:
1. Schedule the job on a Kubernetes cluster with EFA-enabled nodes
2. Launch Pods with the required EFA devices
3. Execute the NCCL performance test with EFA networking
4. Output performance metrics showing the benefits of EFA for distributed training

> **NOTE:**
> We can turn off EFA with `nccl_efa.yaml` by passing an env:
> ```bash
> sky launch -c efa --env USE_EFA=false nccl_efa.yaml
> ```

#### Benchmark results

We compare the performance with and without EFA using NCCL test reports on the same HyperPod cluster (2x p4d.24xlarge, i.e. 2xA100:8).

The `Speed-up` column is calculated by `busbw EFA (GB/s) / busbw Non-EFA (GB/s)`.

| Message Size | busbw EFA (GB/s) | busbw Non-EFA (GB/s) | Speed-up |
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

#### What stands out

| Range         | Observation                                                                                                                                     |
|---------------|-------------------------------------------------------------------------------------------------------------------------------------------------|
| ≤ 256 KB       | Virtually no difference — bandwidth is dominated by software/latency overhead and doesn’t reach the network’s limits. |
| 512 KB – 16 MB | EFA gradually pulls ahead, hitting ~3–6 × by a few MB. |
| ≥ 32 MB       | The fabric really kicks in: ≥ 8 x at 32 MB, climbing to ~18 x for 1–2 GB messages. Non-EFA tops out around 4 GB/s, while EFA pushes ≈ 77 GB/s. |

EFA provides much higher throughput than the traditional TCP transport. Enabling EFA could enhance the performance of inter-instance communication significantly, which could speedup distributed AI training and inference.

## Using EFA on AWS VM

For the instance types listed in the GPU and EFA count mapping table in the [Adding EFA configurations in SkyPilot YAML](#adding-efa-configurations-in-skypilot-yaml) section, the EFA can be enabled by setting `resources.network_tier: best` in the task YAML.

```yaml
resources:
  network_tier: best
```

To run the NCCL test with EFA support with AWS VM:

```bash
sky launch -c efa efa_vm.yaml
```

Check the [`efa_vm.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/aws_efa/efa_vm.yaml) for the complete SkyPilot cluster yaml configurations.

### Benchmark results

We compare the performance with and without EFA using NCCL test reports with the same resources (2x p4d.24xlarge, i.e. 2xA100:8).

The `Speed-up 1 EFA vs 1 ENI` column is calculated by `busbw with 1 EFA Interface (GB/s) / busbw Non-EFA with 1 Network Interface (GB/s)` and the `Speed-up 4 EFA vs 1 ENI` column is calculated by `busbw with 4 EFA Interfaces (GB/s) / busbw Non-EFA with 1 Network Interface (GB/s)`.

| Message Size | busbw with 4 EFA Interfaces (GB/s) | busbw with 1 EFA Interface (GB/s) | busbw Non-EFA with 1 Network Interface (GB/s) | Speed-up 1 EFA vs 1 ENI | Speed-up 4 EFA vs 1 ENI|
|--------------|-------------|------------|-----------|----------|----------|
| 8 B          | 0           | 0          | 0         | -        | -        |
| 16 B         | 0           | 0          | 0         | -        | -        |
| 32 B         | 0           | 0          | 0         | -        | -        |
| 64 B         | 0           | 0          | 0         | -        | -        |
| 128 B        | 0           | 0          | 0         | -        | -        |
| 256 B        | 0           | 0          | 0         | -        | -        |
| 512 B        | 0.01        | 0.01       | 0.01      | 1 x      | 1 x      |
| 1 KB         | 0.01        | 0.01       | 0.02      | 0.5 x    | 0.5 x    |
| 2 KB         | 0.02        | 0.02       | 0.03      | 0.6 x    | 0.6 x    |
| 4 KB         | 0.04        | 0.04       | 0.05      | 0.8 x    | 0.8 x    |
| 8 KB         | 0.09        | 0.08       | 0.08      | 1.0 x    | 1.1 x    |
| 16 KB        | 0.16        | 0.15       | 0.10      | 1.5 x    | 1.6 x    |
| 32 KB        | 0.28        | 0.26       | 0.16      | 1.6 x    | 1.7 x    |
| 64 KB        | 0.54        | 0.50       | 0.29      | 1.7 x    | 1.8 x    |
| 128 KB       | 1.07        | 0.81       | 0.45      | 1.8 x    | 2.4 x    |
| 256 KB       | 2.02        | 1.23       | 0.74      | 1.6 x    | 2.7 x    |
| 512 KB       | 3.28        | 1.70       | 0.85      | 2.0 x    | 3.8 x    |
| 1 MB         | 4.97        | 2.34       | 1.52      | 1.5 x    | 3.2 x    |
| 2 MB         | 6.77        | 2.80       | 2.35      | 1.2 x    | 2.9 x    |
| 4 MB         | 9.28        | 4.96       | 3.79      | 1.3 x    | 2.4 x    |
| 8 MB         | 10.99       | 8.21       | 5.37      | 1.5 x    | 2.0 x    |
| 16 MB        | 19.63       | 11.84      | 6.46      | 1.8 x    | 3.0 x    |
| 32 MB        | 32.62       | 14.93      | 7.27      | 2.0 x    | 4.5 x    |
| 64 MB        | 46.11       | 17.27      | 6.83      | 2.5 x    | 6.7 x    |
| 128 MB       | 57.79       | 18.67      | 7.93      | 2.3 x    | 7.3 x    |
| 256 MB       | 67.20       | 19.59      | 8.03      | 2.4 x    | 8.3 x    |
| 512 MB       | 72.90       | 19.99      | 8.14      | 2.4 x    | 8.9 x    |
| 1 GB         | 76.14       | 20.19      | 8.15      | 2.5 x    | 9.3 x    |
| 2 GB         | 77.90       | 20.31      | 8.20      | 2.5 x    | 9.5 x    |

From the above benchmark results, we can see that:

- EFA brings little benefit for small messages, but gains grow with message size.
- Bandwidth scales near-linearly with multiple EFAs, reaching ~78 GB/s with 4 interfaces.
- On p4d.24xlarge (A100×8), 4 EFAs deliver up to ~9.5× higher bandwidth vs a single ENI.

So EFA is critical for scalable, high-throughput training workloads.
