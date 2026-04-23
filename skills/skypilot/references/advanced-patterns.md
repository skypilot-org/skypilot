# SkyPilot Advanced Patterns Reference

This reference covers advanced SkyPilot patterns for multi-cloud strategies,
distributed training, managed jobs, serving, Kubernetes integration, API server
setup, and cost optimization.

---

## 1. Multi-Cloud Strategies

SkyPilot can automatically select the cheapest cloud/region/zone for a
workload, or follow explicit priority ordering. The key mechanisms are:
- `any_of`: try all listed resource options, optimize for cost (unordered)
- `ordered`: try resource options in strict priority order (first match wins)
- `infra:` field with wildcard patterns for flexible cloud/region/zone targeting

### 1.1 any_of -- Cheapest Available GPU

Use `any_of` when you have multiple acceptable resource configurations and
want SkyPilot to pick the cheapest one available across all of them.

Note: If all options use the same accelerator, you can simply specify
`accelerators:` without `any_of` — SkyPilot will automatically search
across all enabled clouds. Use `any_of` when mixing different GPU types:

```yaml
# cheapest-gpu.yaml — mix different GPU types, SkyPilot picks cheapest
name: cheapest-gpu-training

resources:
  any_of:
    - accelerators: H100:8
    - accelerators: A100-80GB:8
    - accelerators: A100:8

run: |
  torchrun --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE train.py
```

You can also mix different GPU types across clouds:

```yaml
# mixed-gpus.yaml
name: mixed-gpu-training

resources:
  any_of:
    - infra: aws
      accelerators: A10g:4
    - infra: gcp
      accelerators: L4:4
    - infra: azure
      accelerators: A100:1
    - infra: lambda
      accelerators: A100:1

run: |
  python train.py
```

The optimizer evaluates all options together, considering current prices,
availability, and any quotas, then launches on the cheapest option.

### 1.2 ordered -- Strict Priority Fallback

Use `ordered` when you have a strict preference. SkyPilot tries each option
in order and uses the first one that succeeds.

```yaml
# priority-fallback.yaml
name: priority-training

resources:
  ordered:
    - infra: aws/us-east-1
      accelerators: A100:8
    - infra: gcp/us-central1
      accelerators: A100:8
    - infra: aws/us-west-2
      accelerators: A100:4
    - infra: gcp/europe-west4
      accelerators: A100:4

run: |
  python train.py
```

This is useful when you have reserved instances or negotiated pricing on a
specific cloud/region and want to use those first.

### 1.3 Kubernetes + Cloud Fallback

A common pattern is to try on-prem Kubernetes first, then fall back to cloud:

```yaml
# k8s-cloud-fallback.yaml
name: k8s-with-cloud-fallback

resources:
  ordered:
    # Try on-prem K8s cluster first (free/cheaper)
    - infra: k8s/my-onprem-context
      accelerators: A100:4
    # Fall back to cloud K8s
    - infra: k8s/gke-prod-cluster
      accelerators: A100:4
    # Fall back to cloud VMs
    - infra: aws
      accelerators: A100:4
    - infra: gcp
      accelerators: A100:4

run: |
  python train.py
```

### 1.4 SSH Node Pools

SkyPilot can use your own bare-metal machines or VMs via SSH node pools.
First, define node pools in `~/.sky/ssh_node_pools.yaml`:

```yaml
# ~/.sky/ssh_node_pools.yaml
my-gpu-cluster:
  hosts:
    - 10.0.1.10
    - 10.0.1.11
    - 10.0.1.12

my-inference-box:
  hosts:
    - gpu-server.internal.example.com
```

Then reference them in your task YAML:

```yaml
# ssh-nodes.yaml
name: train-on-ssh-nodes

resources:
  infra: ssh/my-gpu-cluster
  accelerators: A100:8

run: |
  python train.py
```

SSH node pools can also participate in `any_of` or `ordered` fallback:

```yaml
resources:
  ordered:
    - infra: ssh/my-gpu-cluster
      accelerators: A100:8
    - infra: aws
      accelerators: A100:8
```

### 1.5 Wildcard Patterns in infra

The `infra` field supports `*` as a wildcard for any component
(cloud, region, zone):

```yaml
# Any cloud, specific zone
resources:
  infra: "*/us-east-1/us-east-1a"
  accelerators: A100:4
```

```yaml
# Specific cloud, any region, specific zone suffix
resources:
  infra: "aws/*/us-east-1a"
  accelerators: A100:4
```

```yaml
# Any cloud, any region (equivalent to not specifying infra)
resources:
  infra: "*"
  accelerators: A100:4
```

The infra format is `cloud[/region[/zone]]`. For Kubernetes, the format is
`k8s/context-name` (context names may contain slashes).

### 1.6 Multi-Region Strategies

Target a specific region across any cloud, or multiple specific regions:

```yaml
# Multi-region with any_of
resources:
  any_of:
    - infra: aws/us-east-1
      accelerators: H100:8
    - infra: aws/us-west-2
      accelerators: H100:8
    - infra: gcp/us-central1
      accelerators: H100:8
    - infra: gcp/us-east4
      accelerators: H100:8
```

For single-cloud multi-region with the same GPU:

```yaml
# Will automatically try all available regions for this cloud
resources:
  infra: aws
  accelerators: H100:8
```

SkyPilot's optimizer automatically considers all regions within the specified
cloud and picks the cheapest available one. You only need `any_of` or
`ordered` when you want to cross cloud boundaries or mix GPU types.

### 1.7 Accelerator Sets and Lists

For single-resource blocks, you can use accelerator sets (unordered, cost-
optimized) or lists (ordered, priority-based):

```yaml
# Unordered set: SkyPilot picks the cheapest available
resources:
  accelerators: {A100:1, V100:1, L4:1, T4:1}
```

```yaml
# Ordered list: SkyPilot tries in order
resources:
  accelerators: [A100:1, V100:1, L4:1, T4:1]
```

---

## 2. Distributed Training

SkyPilot supports multi-node distributed training with automatic cluster
setup. It provides environment variables on every node for coordination.

### 2.1 SkyPilot Environment Variables for Distributed Training

On each node, SkyPilot sets:

| Variable | Description |
|---|---|
| `SKYPILOT_NODE_IPS` | Newline-separated list of all node IPs (head node first) |
| `SKYPILOT_NODE_RANK` | 0-indexed rank of the current node (head = 0) |
| `SKYPILOT_NUM_NODES` | Total number of nodes in the cluster |
| `SKYPILOT_NUM_GPUS_PER_NODE` | Number of GPUs on the current node |

### 2.2 PyTorch DDP Multi-Node Setup

The standard pattern uses `torchrun` with SkyPilot's environment variables:

```yaml
# ddp-training.yaml
name: pytorch-ddp

resources:
  accelerators: A100:8

num_nodes: 4

setup: |
  pip install torch torchvision

run: |
  HEAD_IP=$(echo "$SKYPILOT_NODE_IPS" | head -n1)
  NUM_NODES=$(echo "$SKYPILOT_NODE_IPS" | wc -l)

  torchrun \
    --nnodes=$NUM_NODES \
    --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE \
    --node_rank=$SKYPILOT_NODE_RANK \
    --master_addr=$HEAD_IP \
    --master_port=12355 \
    train_ddp.py \
      --epochs 100 \
      --batch-size 256
```

A minimal `train_ddp.py` would use:

```python
import os

import torch
import torch.distributed as dist

dist.init_process_group(backend='nccl')
local_rank = int(os.environ['LOCAL_RANK'])
torch.cuda.set_device(local_rank)
# ... training loop
```

### 2.3 DeepSpeed ZeRO-3 Multi-Node

```yaml
# deepspeed-zero3.yaml
name: deepspeed-training

resources:
  accelerators: A100-80GB:8

num_nodes: 2

file_mounts:
  /workspace: .

setup: |
  pip install deepspeed torch transformers accelerate

run: |
  HEAD_IP=$(echo "$SKYPILOT_NODE_IPS" | head -n1)
  NUM_NODES=$(echo "$SKYPILOT_NODE_IPS" | wc -l)

  # Create hostfile for DeepSpeed
  echo "$SKYPILOT_NODE_IPS" | while read ip; do
    echo "$ip slots=$SKYPILOT_NUM_GPUS_PER_NODE"
  done > /tmp/hostfile

  if [ "$SKYPILOT_NODE_RANK" == "0" ]; then
    deepspeed \
      --hostfile /tmp/hostfile \
      --master_addr $HEAD_IP \
      --master_port 29500 \
      --num_nodes $NUM_NODES \
      --num_gpus $SKYPILOT_NUM_GPUS_PER_NODE \
      /workspace/train.py \
        --deepspeed /workspace/ds_config_zero3.json \
        --model_name_or_path meta-llama/Llama-2-7b-hf \
        --per_device_train_batch_size 2 \
        --gradient_accumulation_steps 4
  fi
```

Example `ds_config_zero3.json`:

```json
{
  "bf16": { "enabled": true },
  "zero_optimization": {
    "stage": 3,
    "offload_optimizer": { "device": "none" },
    "offload_param": { "device": "none" },
    "overlap_comm": true,
    "contiguous_gradients": true,
    "reduce_bucket_size": 5e7,
    "stage3_prefetch_bucket_size": 5e7,
    "stage3_param_persistence_threshold": 1e5
  },
  "gradient_accumulation_steps": 4,
  "train_micro_batch_size_per_gpu": 2,
  "wall_clock_breakdown": false
}
```

### 2.4 Ray Train Integration

SkyPilot includes a helper script to start a Ray cluster across nodes. Use it
for Ray Train, Ray Tune, and other Ray-based distributed workloads:

```yaml
# ray-train.yaml
name: ray-distributed-training

resources:
  accelerators: A100:8
  ports:
    - 8280  # Ray dashboard

num_nodes: 2

envs:
  HF_HUB_ENABLE_HF_TRANSFER: "1"

setup: |
  pip install "ray[default,train]" torch transformers

run: |
  head_ip=$(echo "$SKYPILOT_NODE_IPS" | head -n1)

  # Use the built-in SkyPilot Ray start script
  export RAY_HEAD_PORT=6385
  export RAY_DASHBOARD_PORT=8280
  export RAY_DASHBOARD_HOST=0.0.0.0
  export RAY_HEAD_IP_ADDRESS="$head_ip"
  ~/sky_templates/ray/start_cluster

  if [ "$SKYPILOT_NODE_RANK" == "0" ]; then
    export RAY_ADDRESS="http://localhost:8280"
    ray job submit --address="$RAY_ADDRESS" \
      -- python train_with_ray.py
  fi
```

### 2.5 FSDP Configuration

PyTorch Fully Sharded Data Parallel (FSDP) with SkyPilot:

```yaml
# fsdp-training.yaml
name: fsdp-llama

resources:
  accelerators: A100-80GB:8

num_nodes: 2

setup: |
  pip install torch transformers accelerate

run: |
  HEAD_IP=$(echo "$SKYPILOT_NODE_IPS" | head -n1)
  NUM_NODES=$(echo "$SKYPILOT_NODE_IPS" | wc -l)
  TOTAL_GPUS=$((NUM_NODES * SKYPILOT_NUM_GPUS_PER_NODE))

  accelerate launch \
    --num_processes $TOTAL_GPUS \
    --num_machines $NUM_NODES \
    --machine_rank $SKYPILOT_NODE_RANK \
    --main_process_ip $HEAD_IP \
    --main_process_port 29500 \
    --use_fsdp \
    --fsdp_auto_wrap_policy TRANSFORMER_BASED_WRAP \
    --fsdp_sharding_strategy FULL_SHARD \
    --fsdp_state_dict_type SHARDED_STATE_DICT \
    --mixed_precision bf16 \
    train_fsdp.py \
      --model_name meta-llama/Llama-2-7b-hf \
      --per_device_train_batch_size 2
```

### 2.6 NCCL Environment Tuning

For multi-node GPU training, NCCL configuration is critical for performance.
Set these via `envs:` in your YAML:

```yaml
# nccl-tuned.yaml
name: nccl-optimized-training

resources:
  accelerators: H100:8

num_nodes: 4

envs:
  # Debug: set to INFO or WARN for troubleshooting
  NCCL_DEBUG: WARN
  # Network interface selection (check with `ip addr` on your nodes)
  NCCL_SOCKET_IFNAME: eth0
  # Use tree algorithm for better all-reduce on large clusters
  NCCL_ALGO: Tree
  # Avoid CUDA stream recording overhead
  TORCH_NCCL_AVOID_RECORD_STREAMS: "1"
  # InfiniBand settings (for clouds with IB/RDMA)
  NCCL_IB_DISABLE: "0"
  NCCL_IB_GID_INDEX: 3
  # Cross-node buffer sizes
  NCCL_BUFFSIZE: "8388608"
  # P2P transport (NVLink within node, network across nodes)
  NCCL_P2P_LEVEL: NVL

run: |
  HEAD_IP=$(echo "$SKYPILOT_NODE_IPS" | head -n1)
  torchrun \
    --nnodes=$SKYPILOT_NUM_NODES \
    --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE \
    --node_rank=$SKYPILOT_NODE_RANK \
    --master_addr=$HEAD_IP \
    --master_port=12355 \
    train.py
```

### 2.7 Common Distributed Training Gotchas

1. **Port conflicts**: Always use a non-default master port (not 29500)
   if running multiple training jobs on the same cluster. Or use SkyPilot's
   port allocation.

2. **Head node vs worker pattern**: Only the head node (`SKYPILOT_NODE_RANK
   == 0`) should launch the training coordinator (e.g., DeepSpeed launcher,
   Ray job submit). Worker nodes run the setup + the Ray/torchrun rendezvous.

3. **Hanging at initialization**: Usually caused by NCCL not finding the right
   network interface. Set `NCCL_SOCKET_IFNAME` to the correct interface.
   Use `NCCL_DEBUG=INFO` to diagnose.

4. **OOM on multi-node**: Gradient synchronization buffers consume extra
   memory. Reduce per-device batch size or use gradient accumulation.

5. **Run section runs on ALL nodes**: The `run:` section executes on every
   node in the cluster. Use `if [ "$SKYPILOT_NODE_RANK" == "0" ]` to gate
   head-only logic (like launching the coordinator or downloading data).

6. **Setup runs on ALL nodes**: The `setup:` section also runs on all nodes.
   This is typically correct (install dependencies everywhere), but be aware
   of it when doing one-time data preparation.

---

## 3. Managed Jobs Production Patterns

Managed jobs (`sky jobs launch`) provide automatic recovery from preemptions,
spot instance failures, and transient errors. They run on a lightweight jobs
controller that monitors and relaunches your workload.

### 3.1 job_recovery Configuration

The `job_recovery` field controls how managed jobs handle failures.

**Strategy options:**
- `EAGER_NEXT_REGION` (default): On preemption, immediately try a different
  region/cloud before retrying the same region.
- `FAILOVER`: On preemption, first retry in the same region, then fail over
  to other regions.

**Full configuration as a dict:**

```yaml
# managed-job-recovery.yaml
name: resilient-training

resources:
  accelerators: A100:8
  use_spot: true
  job_recovery:
    strategy: EAGER_NEXT_REGION
    max_restarts_on_errors: 3
    recover_on_exit_codes: [1, 137]

run: |
  python train.py --checkpoint-dir /ckpts
```

**Shorthand (strategy only):**

```yaml
resources:
  accelerators: A100:8
  use_spot: true
  job_recovery: FAILOVER
```

**Field reference:**

| Field | Type | Default | Description |
|---|---|---|---|
| `strategy` | string | `EAGER_NEXT_REGION` | Recovery strategy name |
| `max_restarts_on_errors` | int | 0 | Max restarts on non-preemption errors |
| `recover_on_exit_codes` | int or list[int] | none | Exit codes that always trigger recovery (ignores max_restarts_on_errors counter) |

When `recover_on_exit_codes` is set, any job failure with a matching exit code
triggers recovery without counting against `max_restarts_on_errors`. This is
useful for known transient failures.

### 3.2 Checkpointing Patterns

For spot instances, checkpointing to cloud storage is essential. The pattern
is: save checkpoints periodically to a mounted bucket, and on restart, resume
from the latest checkpoint.

```yaml
# checkpointed-training.yaml
name: checkpointed-llm-training

resources:
  accelerators: A100-80GB:8
  use_spot: true
  job_recovery:
    strategy: EAGER_NEXT_REGION
    max_restarts_on_errors: 5

file_mounts:
  /ckpts:
    name: my-training-checkpoints  # S3/GCS/R2 bucket
    mode: MOUNT

run: |
  # Resume from latest checkpoint if it exists
  CKPT_ARG=""
  LATEST=$(ls -td /ckpts/checkpoint-* 2>/dev/null | head -1)
  if [ -n "$LATEST" ]; then
    echo "Resuming from checkpoint: $LATEST"
    CKPT_ARG="--resume_from_checkpoint $LATEST"
  fi

  python train.py \
    --output_dir /ckpts \
    --save_steps 500 \
    $CKPT_ARG
```

Launch as a managed job:

```bash
sky jobs launch checkpointed-training.yaml
```

### 3.3 Hyperparameter Sweeps as Managed Jobs

Launch multiple managed jobs for a hyperparameter sweep. Each job runs
independently with automatic recovery:

```bash
# Launch sweep via CLI
for lr in 1e-4 5e-5 1e-5; do
  for bs in 16 32 64; do
    sky jobs launch sweep.yaml \
      --env LR=$lr \
      --env BATCH_SIZE=$bs \
      -n "sweep-lr${lr}-bs${bs}" \
      -y
  done
done
```

```yaml
# sweep.yaml
name: hp-sweep

resources:
  accelerators: A100:4
  use_spot: true
  job_recovery:
    strategy: EAGER_NEXT_REGION
    max_restarts_on_errors: 2

envs:
  LR: 1e-4
  BATCH_SIZE: 32

file_mounts:
  /results:
    name: sweep-results-bucket
    mode: MOUNT

run: |
  python train.py \
    --learning-rate $LR \
    --batch-size $BATCH_SIZE \
    --output-dir "/results/lr${LR}_bs${BATCH_SIZE}"
```

Monitor all running jobs:

```bash
sky jobs queue -o json
```

### 3.4 Job Pools (Experimental)

Job pools pre-provision clusters that multiple jobs can be submitted to,
avoiding cold-start overhead. Pools are defined with a `service:` section
containing `pool:` configuration.

**Create a pool:**

```yaml
# gpu-pool.yaml
name: gpu-pool

resources:
  accelerators: A100:8

service:
  pool:
    workers: 3
```

```bash
# Apply the pool configuration
sky jobs pool apply gpu-pool.yaml -p my-gpu-pool
```

**Submit jobs to the pool:**

```bash
sky jobs launch job.yaml --pool my-gpu-pool
```

**Pool with autoscaling:**

```yaml
# autoscaling-pool.yaml
name: autoscaling-pool

resources:
  accelerators: A100:4

service:
  pool:
    min_workers: 1
    max_workers: 10
    queue_length_threshold: 2
    upscale_delay_seconds: 60
    downscale_delay_seconds: 300
```

**Pool management commands:**

```bash
# Check pool status
sky jobs pool status

# Check a specific pool
sky jobs pool status my-gpu-pool

# Update number of workers
sky jobs pool apply -p my-gpu-pool --workers 5

# Delete a pool
sky jobs pool down my-gpu-pool

# Delete all pools
sky jobs pool down -a
```

---

## 4. SkyServe Advanced

SkyServe deploys model serving endpoints with autoscaling, multi-cloud
replicas, rolling updates, and spot instance support.

### 4.1 Autoscaling Configuration

```yaml
# autoscaling-service.yaml
name: llm-service

resources:
  accelerators: A100:1
  ports:
    - 8080

service:
  readiness_probe:
    path: /health
    initial_delay_seconds: 300
    timeout_seconds: 10
  replica_policy:
    min_replicas: 2
    max_replicas: 10
    target_qps_per_replica: 5.0
    upscale_delay_seconds: 120
    downscale_delay_seconds: 600

run: |
  python -m vllm.entrypoints.openai.api_server \
    --model meta-llama/Llama-2-7b-hf \
    --port 8080
```

**Autoscaling field reference:**

| Field | Type | Default | Description |
|---|---|---|---|
| `min_replicas` | int | 1 | Minimum number of replicas |
| `max_replicas` | int | same as min | Maximum number of replicas |
| `target_qps_per_replica` | float | required if max != min | Target QPS per replica for scaling |
| `upscale_delay_seconds` | int | 300 | Seconds target must exceed before scaling up |
| `downscale_delay_seconds` | int | 1200 | Seconds target must be below before scaling down |
| `num_overprovision` | int | none | Extra replicas beyond what autoscaling calculates |

Deploy and manage:

```bash
# Deploy the service
sky serve up autoscaling-service.yaml -n my-llm

# Check service status
sky serve status my-llm

# Get the service endpoint
sky serve status my-llm --endpoint

# Tail controller logs
sky serve logs --controller my-llm
```

### 4.2 Rolling Updates vs Blue-Green Updates

**Rolling update** (default): Gradually replaces replicas one by one. Some
old and new replicas serve traffic simultaneously during the transition.

```bash
# Update with rolling strategy (default)
sky serve update my-llm updated-service.yaml

# Explicitly specify rolling
sky serve update --mode rolling my-llm updated-service.yaml
```

**Blue-green update**: Provisions all new replicas first, then switches
traffic atomically. No mixed versions serve traffic simultaneously.

```bash
# Blue-green update
sky serve update --mode blue_green my-llm updated-service.yaml
```

Blue-green requires enough quota to run both old and new replicas
simultaneously during the transition.

### 4.3 Spot Replicas for Cost Savings

Use spot instances for serving replicas with automatic on-demand fallback:

```yaml
# spot-serving.yaml
name: spot-llm-service

resources:
  accelerators: A100:1
  use_spot: true
  ports:
    - 8080

service:
  readiness_probe: /health
  replica_policy:
    min_replicas: 3
    max_replicas: 8
    target_qps_per_replica: 5.0
    # Maintain 1 on-demand replica as a baseline
    base_ondemand_fallback_replicas: 1
    # Dynamically add on-demand replicas if spot is unavailable
    dynamic_ondemand_fallback: true

run: |
  python serve.py --port 8080
```

### 4.4 TLS/HTTPS Configuration

Enable TLS termination at the load balancer:

```yaml
# tls-service.yaml
name: secure-service

resources:
  accelerators: L4:1
  ports:
    - 8080

file_mounts:
  /certs/server.key: ./certs/server.key
  /certs/server.crt: ./certs/server.crt

service:
  readiness_probe: /health
  replicas: 2
  tls:
    keyfile: /certs/server.key
    certfile: /certs/server.crt

run: |
  python serve.py --port 8080
```

### 4.5 Readiness Probe Configuration

The readiness probe determines when a replica is ready to receive traffic.

**Simple GET probe (most common):**

```yaml
service:
  readiness_probe: /health
  replicas: 2
```

**Full probe configuration:**

```yaml
service:
  readiness_probe:
    path: /v1/models
    initial_delay_seconds: 600
    timeout_seconds: 30
    headers:
      Authorization: "Bearer test-token"
  replicas: 1
```

**POST probe (useful for LLMs that need a generation test):**

```yaml
service:
  readiness_probe:
    path: /v1/completions
    post_data:
      model: llama-2-7b
      prompt: "Hello"
      max_tokens: 1
    initial_delay_seconds: 900
    timeout_seconds: 60
  replicas: 1
```

**Default values:**
- `initial_delay_seconds`: 1200 (20 minutes) -- readiness failures during
  this period are ignored
- `timeout_seconds`: 15

### 4.6 Multi-Cloud Replicas

Deploy replicas across multiple clouds for availability and cost optimization:

```yaml
# multi-cloud-serving.yaml
name: multi-cloud-llm

resources:
  any_of:
    - infra: aws
      accelerators: A100:1
    - infra: gcp
      accelerators: A100:1
    - infra: azure
      accelerators: A100:1
  ports:
    - 8080

service:
  readiness_probe: /health
  replica_policy:
    min_replicas: 3
    max_replicas: 10
    target_qps_per_replica: 5.0

run: |
  python serve.py --port 8080
```

SkyPilot distributes replicas across available clouds for redundancy and
picks the cheapest options.

### 4.7 Load Balancing Policies

SkyServe supports different load balancing policies:

```yaml
service:
  readiness_probe: /health
  replica_policy:
    min_replicas: 2
    max_replicas: 8
    target_qps_per_replica: 5.0
  load_balancing_policy: round_robin
```

For heterogeneous GPU deployments, use instance-aware load balancing:

```yaml
# Instance-aware balancing across different GPU types
resources:
  any_of:
    - accelerators: H100:1
    - accelerators: A100:1
  ports:
    - 8080

service:
  readiness_probe: /health
  replica_policy:
    min_replicas: 2
    max_replicas: 10
    target_qps_per_replica:
      H100: 10.0
      A100: 5.0
  load_balancing_policy: instance_aware_least_load
```

---

## 5. Kubernetes Integration

SkyPilot treats Kubernetes clusters as first-class infrastructure alongside
cloud providers.

### 5.1 Using K8s Contexts

SkyPilot uses your kubeconfig contexts. Reference them with `infra: k8s/`:

```yaml
# Use a specific K8s context
resources:
  infra: k8s/my-cluster-context
  accelerators: A100:4
```

```yaml
# Use the default K8s context (current-context in kubeconfig)
resources:
  infra: kubernetes
  accelerators: A100:4
```

```yaml
# EKS context (contexts can contain special characters)
resources:
  infra: k8s/arn:aws:eks:us-east-1:123456789012:cluster/my-cluster
  accelerators: A100:4
```

### 5.2 Pod Config Overrides

Customize the pod spec for SkyPilot pods via `~/.sky/config.yaml`. This
allows setting tolerations, nodeSelectors, labels, and other Kubernetes-
specific configurations.

```yaml
# ~/.sky/config.yaml
kubernetes:
  allowed_contexts:
    - my-production-cluster
    - my-dev-cluster
  context_configs:
    my-production-cluster:
      pod_config:
        spec:
          tolerations:
            - key: "nvidia.com/gpu"
              operator: "Exists"
              effect: "NoSchedule"
          nodeSelector:
            node-type: gpu
            team: ml-training
          containers:
            - name: skypilot
              resources:
                limits:
                  nvidia.com/gpu: "1"
        metadata:
          labels:
            team: ml-platform
            environment: production
      custom_metadata:
        labels:
          app: skypilot
          managed-by: skypilot
    my-dev-cluster:
      pod_config:
        spec:
          nodeSelector:
            node-type: dev-gpu
```

### 5.3 Multi-Cluster K8s with any_of

Spread workloads across multiple Kubernetes clusters:

```yaml
# multi-k8s.yaml
name: multi-cluster-training

resources:
  any_of:
    - infra: k8s/us-east-cluster
      accelerators: A100:8
    - infra: k8s/eu-west-cluster
      accelerators: A100:8
    - infra: k8s/asia-cluster
      accelerators: H100:8

run: |
  python train.py
```

Combined with cloud fallback:

```yaml
resources:
  ordered:
    - infra: k8s/onprem-cluster
      accelerators: A100:8
    - infra: k8s/gke-cluster
      accelerators: A100:8
    - infra: aws
      accelerators: A100:8
```

### 5.4 GPU Detection and Labeling

SkyPilot needs GPU labels on Kubernetes nodes to schedule GPU workloads.
Use `sky gpus label` to automatically detect and label GPUs:

```bash
# Label GPUs in the current Kubernetes context
sky gpus label

# Label GPUs in a specific context
sky gpus label --context my-k8s-cluster

# Start labeling without waiting for completion
sky gpus label --async

# Cleanup labeling resources when done
sky gpus label --cleanup
```

Check available GPUs across all configured infrastructure:

```bash
# List all available GPUs
sky gpus list

# Show GPUs for a specific type
sky gpus list A100

# Show GPUs available on Kubernetes
sky gpus list --infra kubernetes
```

### 5.5 Kubernetes Configuration in ~/.sky/config.yaml

```yaml
# ~/.sky/config.yaml
kubernetes:
  # Restrict which contexts SkyPilot can use
  allowed_contexts:
    - gke-prod
    - eks-staging

  # Port exposure mode
  ports: loadbalancer  # or nodeport, ingress, podip

  # Cluster autoscaler integration
  autoscaler: gke  # or karpenter, generic

  # Per-context configuration
  context_configs:
    gke-prod:
      provision_timeout: 600
      pod_config:
        spec:
          nodeSelector:
            cloud.google.com/gke-accelerator: nvidia-tesla-a100
```

---

## 6. API Server and Team Setup

The SkyPilot API server enables team collaboration by providing a shared
control plane for launching and managing clusters, jobs, and services.

### 6.1 Local API Server

For individual use or development:

```bash
# Start the local API server
sky api start

# Check server status
sky api status

# Stop the server
sky api stop
```

The local API server runs on port 46580 by default. It starts automatically
when you run any `sky` command.

### 6.2 Remote API Server with Helm

Deploy a shared API server on Kubernetes for team use:

```bash
# Add the SkyPilot Helm repo
helm repo add skypilot https://helm.skypilot.co
helm repo update

# Build chart dependencies
helm dependency build ./charts/skypilot

# Deploy the API server
NAMESPACE=skypilot
helm upgrade --install skypilot ./charts/skypilot --devel \
  --namespace $NAMESPACE \
  --create-namespace
```

For custom images (e.g., testing local changes):

```bash
# Build and push custom image
DOCKER_IMAGE=my-repo/skypilot:v1
docker buildx build --push --platform linux/amd64 \
  -t $DOCKER_IMAGE -f Dockerfile .

# Deploy with custom image
helm upgrade --install skypilot ./charts/skypilot --devel \
  --namespace skypilot \
  --create-namespace \
  --set apiService.image=$DOCKER_IMAGE
```

Upgrade an existing deployment (always use `--reuse-values`):

```bash
helm upgrade skypilot ./charts/skypilot -n skypilot --reuse-values \
  --set apiService.image=$DOCKER_IMAGE
```

### 6.3 PostgreSQL Backend

For production deployments, use PostgreSQL instead of SQLite:

```bash
# Create a secret with the database connection string
kubectl create secret generic db-uri -n skypilot \
  --from-literal=uri="postgresql://user:pass@host:5432/skypilot"

# Deploy with PostgreSQL backend
helm upgrade --install skypilot ./charts/skypilot --devel \
  -n skypilot --create-namespace \
  --set apiService.dbConnectionSecretName=db-uri \
  --set storage.enabled=false
```

Or set the connection string directly (less secure, not recommended for
production):

```bash
helm upgrade --install skypilot ./charts/skypilot --devel \
  -n skypilot --create-namespace \
  --set apiService.dbConnectionString="postgresql://user:pass@host:5432/db"
```

### 6.4 Authentication

Connect to a remote API server:

```bash
# OAuth2 browser login
sky api login -e https://skypilot.example.com

# Service account token login
sky api login -e https://skypilot.example.com --token sky_abc123...
```

Check the current connection:

```bash
sky api status
```

---

## 7. Cost Optimization

SkyPilot provides multiple mechanisms to minimize cloud spending while
maximizing GPU availability.

### 7.1 Automatic Cloud Selection (Cheapest)

By default, SkyPilot's optimizer picks the cheapest cloud/region for your
requested resources:

```yaml
# SkyPilot automatically finds the cheapest option
resources:
  accelerators: A100:4
```

```bash
# Preview the optimizer's choice without launching
sky launch --dryrun task.yaml
```

The `--dryrun` flag shows you which cloud, region, instance type, and price
SkyPilot would choose, without actually provisioning anything.

### 7.2 Spot Instances

Spot/preemptible instances offer 60-90% savings over on-demand:

```yaml
resources:
  accelerators: A100:8
  use_spot: true
```

For production spot workloads, combine with managed jobs for automatic
recovery:

```yaml
resources:
  accelerators: A100:8
  use_spot: true
  job_recovery:
    strategy: EAGER_NEXT_REGION
    max_restarts_on_errors: 3
```

```bash
sky jobs launch spot-training.yaml
```

### 7.3 Dryrun to Preview Costs

Always preview costs before launching expensive workloads:

```bash
# Show what would be launched and estimated cost
sky launch --dryrun gpu-training.yaml

# Show cost for a specific cloud
sky launch --dryrun --infra aws gpu-training.yaml
```

### 7.4 Cost Report

Track spending across all clusters:

```bash
# Show cost report for active clusters
sky cost-report

# Show all clusters including terminated (last 30 days)
sky cost-report --all

# Show costs for specific number of days
sky cost-report --days 7
```

### 7.5 Autostop Configuration

Automatically stop or terminate idle clusters to avoid waste. Autostop is
configured in the `resources:` section of your YAML.

**Simple autostop (idle minutes):**

```yaml
resources:
  accelerators: A100:4
  autostop: 30  # Stop after 30 minutes idle
```

**Full autostop configuration:**

```yaml
resources:
  accelerators: A100:4
  autostop:
    idle_minutes: 30
    down: true  # Terminate instead of just stopping
    wait_for: jobs_and_ssh  # Reset timer while jobs or SSH sessions active
```

**wait_for options:**

| Value | Description |
|---|---|
| `jobs_and_ssh` | (default) Wait for running jobs AND open SSH sessions |
| `jobs` | Only wait for running jobs to finish |
| `none` | Stop immediately after idle_minutes, regardless of activity |

**Disable autostop:**

```yaml
resources:
  accelerators: A100:4
  autostop: false
```

**CLI overrides:**

```bash
# Set autostop on an existing cluster
sky autostop my-cluster -i 60

# Set autodown (terminate when idle)
sky autostop my-cluster -i 30 --down

# Cancel autostop
sky autostop my-cluster --cancel
```

### 7.6 Autostop Hooks for Pre-Shutdown Actions

Run a custom script before the cluster stops or terminates. This is useful
for saving state, uploading logs, or graceful shutdown procedures:

```yaml
resources:
  accelerators: A100:4
  autostop:
    idle_minutes: 30
    down: true
    hook: |
      echo "Cluster shutting down, saving state..."
      cp -r /tmp/logs /data/saved-logs/
      python cleanup.py
    hook_timeout: 120  # Seconds to wait for hook to complete
```

The hook runs before the cluster is stopped or terminated. If the hook
exceeds `hook_timeout` seconds, it is killed and the shutdown proceeds.

### 7.7 Combining Cost Optimization Strategies

For maximum savings, combine multiple strategies:

```yaml
# maximum-savings.yaml
name: cost-optimized-training

resources:
  any_of:
    - infra: aws
      accelerators: A100:8
    - infra: gcp
      accelerators: A100:8
    - infra: lambda
      accelerators: A100:8
  use_spot: true
  job_recovery:
    strategy: EAGER_NEXT_REGION
    max_restarts_on_errors: 5
  autostop:
    idle_minutes: 10
    down: true

file_mounts:
  /ckpts:
    name: training-checkpoints
    mode: MOUNT

run: |
  python train.py --checkpoint-dir /ckpts --resume-latest
```

Launch as a managed job for automatic recovery:

```bash
sky jobs launch maximum-savings.yaml
```

This configuration:
1. Uses `any_of` to find the cheapest cloud
2. Uses spot instances for 60-90% savings
3. Recovers automatically from preemptions via managed jobs
4. Auto-terminates idle clusters
5. Checkpoints to cloud storage for seamless recovery
