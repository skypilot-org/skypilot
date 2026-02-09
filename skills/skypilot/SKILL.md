---
name: skypilot
description: Cloud GPU orchestration for AI workloads. Use when launching GPU clusters, running training jobs, serving models, or managing compute across 25+ clouds and Kubernetes.
version: 1.0.0
author: SkyPilot Authors
license: Apache-2.0
tags: [Infrastructure, Multi-Cloud, GPU, Kubernetes, Training, Serving, Cost Optimization, SkyPilot]
dependencies: [skypilot>=0.10.0]
---

# SkyPilot Skill

SkyPilot is a unified framework to run AI workloads on any cloud or Kubernetes. It provides a single interface to launch clusters, run training jobs, and serve models across 25+ clouds (AWS, GCP, Azure, Lambda, RunPod, Kubernetes, and more).

## When to Use SkyPilot

**Use SkyPilot when you need to:**
- Manage compute resources on any cloud or Kubernetes cluster
- Launch GPU/TPU clusters (H100, A100, V100, etc.) on any cloud
- Run training, fine-tuning, or batch inference jobs
- Serve models with autoscaling and multi-cloud replicas (SkyServe)
- Run jobs with automatic recovery from spot preemptions (managed jobs)
- Find the cheapest or most available GPU across clouds

**Don't use SkyPilot for:**
- Local-only workloads (use Docker/conda directly)

## Before You Start (Agent Bootstrap)

Before running any SkyPilot command, verify the environment:

**Step 1: Check if SkyPilot is installed**
```bash
sky --version
```
If `sky` is not found, install it first:
```bash
pip install "skypilot[aws,gcp,kubernetes]"  # Pick clouds the user needs
```
Ask the user which clouds they need if unclear.

**Step 2: Check cloud credentials**
```bash
sky check
```
This shows which clouds are configured. If the user's target cloud is not enabled, guide them through credential setup (see [Troubleshooting](references/troubleshooting.md#1-installation-and-credentials)).

## Quick Start

```bash
# List available GPUs
sky gpus list

# Launch a GPU cluster
sky launch -c mycluster --gpus H100 -- nvidia-smi

# Run a task from YAML
sky launch -c mycluster task.yaml

# SSH into cluster
ssh mycluster

# Tear down
sky down mycluster
```

## Task YAML Structure

The task YAML is SkyPilot's primary interface. All fields are optional.

```yaml
# task.yaml
name: my-training-job

# Local directory to sync to remote ~/sky_workdir
workdir: .

# Number of nodes (for distributed training)
num_nodes: 1

resources:
  # GPU/TPU accelerators (SkyPilot auto-selects the cheapest cloud/region)
  accelerators: H100:8
  # Optional: pin to a specific cloud/region if needed
  # infra: aws  # or aws/us-east-1, k8s, ssh/my-pool
  # Use spot instances for cost savings
  use_spot: false
  # Disk size in GB
  disk_size: 256
  # Open ports for serving
  ports: 8080

# Environment variables
envs:
  MODEL_NAME: my-model
  BATCH_SIZE: 32

# Secrets (redacted in logs/dashboard)
secrets:
  HF_TOKEN: my-huggingface-token

# Setup: runs once on cluster creation, cached on reuse
setup: |
  pip install torch transformers

# Run: the main command
run: |
  python train.py --model $MODEL_NAME --batch-size $BATCH_SIZE
```

## Key CLI Commands

| Command | Description |
|---------|-------------|
| `sky launch` | Launch a cluster or run a task |
| `sky exec` | Execute a task on existing cluster |
| `sky status` | Show cluster status |
| `sky stop` / `sky start` | Stop/start clusters (preserves disk) |
| `sky down` | Tear down clusters |
| `sky autostop` | Configure auto-stop after idle time |
| `sky queue` | Show job queue on a cluster |
| `sky logs` | Stream job logs |
| `sky cancel` | Cancel running jobs |
| `sky gpus list` | Show GPU availability and pricing |
| `sky check` | Check cloud credentials |
| `sky cost-report` | Show cluster cost report |
| `sky jobs launch` | Launch a managed job (auto-recovery) |
| `sky jobs queue` | Show managed job queue |
| `sky serve up` | Start a model serving service |
| `sky serve status` | Show service status |
| `sky api start` | Start the SkyPilot API server |
| `sky dashboard` | Open the SkyPilot web dashboard |

## GPU and Cloud Selection

**IMPORTANT: Let SkyPilot choose the cloud and region.** Do NOT manually pick a cloud/region/instance by parsing `sky gpus list` output. SkyPilot's optimizer automatically selects the cheapest available option across all enabled clouds. Only specify `infra:` when the user explicitly requests a specific cloud or region.

**Default behavior (recommended):** Just specify the GPU type. SkyPilot finds the cheapest cloud/region automatically:

```yaml
resources:
  accelerators: H100:8  # SkyPilot picks the cheapest cloud/region with H100:8
```

If the user doesn't specify a GPU type, ask them what GPU they need (or what model/workload they're running so you can recommend one). Do NOT run `sky gpus list` and pick for them — present options and let the user decide, or use `any_of` to let SkyPilot maximize availability:

```yaml
# Let SkyPilot choose from multiple acceptable GPU types (cheapest wins)
resources:
  any_of:
    - accelerators: H100:8
    - accelerators: A100-80GB:8
    - accelerators: A100:8
```

Use `ordered` only when the user has a strict preference:

```yaml
# Try H100 first on AWS, fall back to GCP, then A100
resources:
  ordered:
    - infra: aws/us-east-1
      accelerators: H100:8
    - infra: gcp/us-central1
      accelerators: H100:8
    - infra: aws/us-west-2
      accelerators: A100-80GB:8
```

Only set `infra:` when the user explicitly says something like "use AWS" or "run on GCP us-central1":

```yaml
resources:
  infra: aws             # User asked for AWS specifically
  accelerators: H100:8
```

## Cluster Lifecycle

```bash
# Launch and run a task
sky launch -c mycluster task.yaml

# Re-run a different task on the same cluster (fast, skips provisioning)
sky exec mycluster another_task.yaml

# Run an inline command
sky exec mycluster -- python train.py --epochs 10

# Set autostop (stop after 30 min idle, preserving disk)
sky autostop mycluster -i 30

# Set autodown (tear down after 30 min idle)
sky autostop mycluster -i 30 --down

# Stop to save costs, restart later
sky stop mycluster
sky start mycluster

# Tear down completely
sky down mycluster

# Tear down all clusters
sky down -a
```

## Managed Jobs (Auto-Recovery from Spot Preemptions)

Use `sky jobs launch` for long-running jobs that should survive spot preemptions:

```yaml
# managed-job.yaml
name: training-with-recovery

resources:
  accelerators: A100:8
  use_spot: true
  job_recovery:
    strategy: FAILOVER             # or EAGER_NEXT_REGION
    max_restarts_on_errors: 3      # Retry on user code errors
    recover_on_exit_codes: [33]    # Always retry these exit codes

run: |
  python train.py --resume-from-checkpoint
```

```bash
# Launch as managed job
sky jobs launch managed-job.yaml

# Check status
sky jobs queue

# Stream logs
sky jobs logs <job_id>

# Cancel
sky jobs cancel <job_id>
```

**Checkpoint pattern**: Your training script should save checkpoints to persistent storage (cloud bucket or volume) and resume from the latest checkpoint on restart. SkyPilot handles the cluster recovery; your script handles the state recovery.

## SkyServe: Model Serving

```yaml
# serve.yaml
resources:
  accelerators: A100:1
  ports: 8080

run: |
  python -m vllm.entrypoints.openai.api_server \
    --model meta-llama/Llama-3.1-8B-Instruct \
    --port 8080

service:
  readiness_probe: /v1/models
  replica_policy:
    min_replicas: 1
    max_replicas: 3
    target_qps_per_replica: 5
```

```bash
# Start service
sky serve up serve.yaml -n my-llm

# Check status
sky serve status my-llm

# Get endpoint
sky serve status my-llm --endpoint

# Update (rolling)
sky serve update my-llm new-serve.yaml

# Tear down
sky serve down my-llm
```

## CLI vs Python SDK

**Use CLI** (`sky launch`, `sky exec`, etc.) for:
- Interactive development and debugging
- One-off cluster management
- Shell scripts and CI/CD pipelines
- Quick iteration on tasks

**Use Python SDK** (`import sky`) for:
- Programmatic workflows (batch submissions, sweeps)
- DAG-based pipelines
- Integration with Python orchestrators
- Complex logic around cluster management

```python
import sky

# All SDK calls return a request ID (future)
task = sky.Task.from_yaml('task.yaml')
request_id = sky.launch(task, cluster_name='my-cluster')
result = sky.get(request_id)  # Wait for completion

# Check cluster status
request_id = sky.status()
clusters = sky.get(request_id)
```

## Common Workflows

### Fine-Tuning Workflow
1. Write task YAML with `setup` (install deps) and `run` (training command)
2. Use `file_mounts` or `workdir` to sync code
3. `sky launch -c train task.yaml` to launch
4. `sky logs train` to monitor
5. `sky exec train -- python eval.py` to evaluate on same cluster
6. `sky down train` when done

### Hyperparameter Sweep
1. Create parameterized YAML with `envs`
2. Launch multiple managed jobs:
   ```bash
   for lr in 1e-4 1e-5 1e-6; do
     sky jobs launch sweep.yaml --env LR=$lr --name sweep-lr-$lr
   done
   ```
3. Monitor with `sky jobs queue`

### Model Serving Deployment
1. Write serve YAML with `service:` section
2. `sky serve up serve.yaml -n my-service`
3. Get endpoint: `sky serve status my-service --endpoint`
4. Update model: `sky serve update my-service updated.yaml`

## Agent Feedback Loop

When using SkyPilot programmatically, follow this loop:

1. **Validate**: `sky launch --dryrun task.yaml` (check resource availability/cost)
2. **Launch**: `sky launch -c mycluster task.yaml`
3. **Monitor**: `sky status` and `sky queue mycluster`
4. **Debug**: `sky logs mycluster` (stream logs) or `ssh mycluster` (interactive)
5. **Iterate**: `sky exec mycluster updated_task.yaml` (run on existing cluster)
6. **Cleanup**: `sky down mycluster`

## Common Issues Quick Reference

| Issue | Solution |
|-------|----------|
| GPU not available | Use `any_of` for fallback, or try different regions/clouds |
| Setup takes too long | SkyPilot caches setup; use `sky exec` to skip it on reruns |
| Task fails silently | Check `sky logs <cluster>` or `ssh <cluster>` to debug |
| Cluster stuck in INIT | `sky down <cluster>` and relaunch |
| Spot preemption | Use `sky jobs launch` with `job_recovery` for auto-recovery |
| Port not accessible | Ensure `ports:` is set in resources and security groups allow traffic |
| File sync slow | Use cloud bucket mounts instead of `workdir` for large datasets |
| Credentials error | Run `sky check` and follow instructions per cloud |

## File Mounts

```yaml
file_mounts:
  # Sync local directory
  /remote/data: /local/data

  # Mount cloud storage (read-only, streaming)
  /remote/dataset:
    source: s3://my-bucket/dataset
    mode: MOUNT

  # Mount cloud storage (cached, read-only, fast random access)
  /remote/model:
    source: gs://my-bucket/model-weights
    mode: MOUNT_CACHED

  # Copy from cloud storage (read-write, full download)
  /remote/code:
    source: s3://my-bucket/code
    mode: COPY
```

## Environment Variables Set by SkyPilot

These are automatically available in `run` and `setup` commands:

| Variable | Description |
|----------|-------------|
| `SKYPILOT_NODE_RANK` | Rank of current node (0-indexed) |
| `SKYPILOT_NODE_IPS` | Newline-separated IPs of all nodes |
| `SKYPILOT_NUM_NODES` | Total number of nodes |
| `SKYPILOT_NUM_GPUS_PER_NODE` | Number of GPUs on this node |
| `SKYPILOT_CLUSTER_INFO` | JSON with cluster metadata |

## References

For detailed reference documentation:

- [CLI Reference](references/cli-reference.md) — All commands and flags
- [YAML Specification](references/yaml-spec.md) — Complete task YAML schema
- [Python SDK](references/python-sdk.md) — Programmatic API
- [Advanced Patterns](references/advanced-patterns.md) — Multi-cloud, distributed training, production patterns
- [Troubleshooting](references/troubleshooting.md) — Error diagnosis and solutions
- [Examples](references/examples.md) — Copy-paste task YAML examples
