---
name: skypilot
description: "Use when launching cloud VMs, Kubernetes pods, or Slurm jobs for GPU/TPU/CPU workloads, training or fine-tuning models on cloud GPUs, deploying inference servers (vllm, TGI, etc.) with autoscaling, writing or debugging SkyPilot task YAML files, using spot/preemptible instances for cost savings, comparing GPU prices across clouds, managing compute across 25+ clouds, Kubernetes, Slurm, and on-prem clusters with failover between them, troubleshooting resource availability or SkyPilot errors, or optimizing cost and GPU availability."
---

# SkyPilot Skill

SkyPilot is a unified framework to run AI workloads on any cloud, Slurm or Kubernetes. It provides a single interface to launch clusters, run jobs, and serve models across 25+ clouds (AWS, GCP, Azure, Coreweave, Nebius, Lambda, Together AI, RunPod, and more), Kubernetes clusters, and Slurm clusters.

## When to Use SkyPilot

**Use SkyPilot when you need to:**
- Manage compute resources on any cloud, Slurm, or Kubernetes cluster
- Launch CPU/GPU/TPU (GB300, GB200, B200, H200, H100, etc.) on any cloud, Kubernetes or Slurm
- Run training, fine-tuning, or batch inference jobs
- Serve models with autoscaling and multi-cloud replicas (SkyServe)
- Run long-running jobs with automatic lifecycle management and recovery (managed jobs)
- Find the cheapest or most available GPU across clouds

**Don't use SkyPilot for:**
- Local-only workloads (use Docker/conda directly)

## Capabilities: When to Use What

SkyPilot has three core abstractions. Use the right one for each stage of your workflow:

**1. SkyPilot Clusters** (`sky launch` / `sky exec`) — Interactive development and debugging
- Use during initial development, debugging, and experimentation
- Launch a cluster, SSH in or connect VSCode/Cursor (`code --remote ssh-remote+CLUSTER`), iterate quickly
- Cluster stays up until you stop/down it or autostop triggers
- Best for: prototyping, debugging, short experiments

**2. Managed Jobs** (`sky jobs launch`) — Long-running training and batch jobs
- Use when submitting long-running jobs that should run unattended
- Manages the full lifecycle: provisioning, execution, recovery, and teardown
- Automatically recovers from spot preemptions, quota limits, and transient failures
- Works across clouds, Kubernetes, and Slurm (handles preemptions and quota)
- Best for: training runs, fine-tuning, hyperparameter sweeps, batch inference

**3. SkyServe** (`sky serve up`) — Production model serving
- Use when serving models at scale with autoscaling
- Start with `sky launch` + open port to test your serving setup, then use `sky serve up` to scale
- Provides load balancing, autoscaling, and multi-cloud replicas
- Best for: model serving endpoints, API services

## Before You Start (Agent Bootstrap)

Bootstrap to confirm SkyPilot is installed, connected to an API server, and has cloud credentials. Once confirmed, skip straight to the user's task.

**Step 1: Check installation and API server connectivity**

```bash
sky api info
```
| Output contains | Meaning | Next action |
|-----------------|---------|-------------|
| Server version and status | Server is running and connected | **Bootstrap done.** Skip to user's task. |
| `No SkyPilot API server is connected` | No server connected | Go to "Start or connect a server" below. |
| `Could not connect to SkyPilot API server` | Remote server unreachable or auth expired | Tell the user and suggest `sky api login --relogin -e <endpoint>` to reconnect. |
| `command not found: sky` | SkyPilot not installed | Go to "Install SkyPilot" below. |

**Install SkyPilot** (only if `sky` command not found):
```bash
pip install "skypilot[aws,gcp,kubernetes]"  # Pick clouds the user needs
```
Ask the user which clouds they need if unclear, then re-run `sky api info`.

**Start or connect a server** (only if "not running"):

Ask the user:
> Do you have an existing SkyPilot API server to connect to, or should I start one locally?

- **Connect to existing server:** `sky api login -e <API_SERVER_URL>` — get the URL from the user.
- **Start locally:** `sky api start`

After either path, re-run `sky api info` to confirm the server is reachable.

**Step 2: Check cloud credentials** (only for fresh setups — skip if the server was already running)
```bash
sky check -o json
```
This shows which clouds are enabled or disabled. If the user's target cloud is not enabled, guide them through credential setup (see [Troubleshooting](references/troubleshooting.md#1-installation-and-credentials)).

## Essential Commands

Use `-o json` with status/query commands to get structured JSON output instead of tables.

**Clusters** — interactive development and debugging:

| Command | Description |
|---------|-------------|
| `sky launch -c NAME task.yaml` | Launch a cluster or run a task |
| `sky exec NAME task.yaml` | Run task on existing cluster (skips provisioning) |
| `sky status -o json` | Show all clusters |
| `sky logs NAME` | Stream job logs from a cluster |
| `sky stop NAME` / `sky start NAME` | Stop/restart to save costs (preserves disk) |
| `sky down NAME` | Tear down a cluster completely |
| `sky gpus list -o json` | List available GPU types across clouds |

**Managed Jobs** — long-running unattended workloads:

| Command | Description |
|---------|-------------|
| `sky jobs launch task.yaml` | Launch a managed job (auto lifecycle + recovery) |
| `sky jobs queue -o json` | Show all managed jobs and their status |
| `sky jobs logs JOB_ID` | Stream logs from a managed job |
| `sky jobs cancel JOB_ID` | Cancel a managed job |

**SkyServe** — model serving with autoscaling:

| Command | Description |
|---------|-------------|
| `sky serve up serve.yaml -n NAME` | Start a model serving service |
| `sky serve status NAME` | Show service status and endpoint URL |
| `sky serve update NAME new.yaml` | Update a running service (rolling) |
| `sky serve down NAME` | Tear down a service |

For complete CLI reference, see [CLI Reference](references/cli-reference.md).

## Quick Start

```bash
# Launch a GPU cluster
sky launch -c mycluster --gpus H100 -- nvidia-smi

# Run a task from YAML
sky launch -c mycluster task.yaml

# SSH into cluster
ssh mycluster

# Connect VSCode or Cursor to the cluster for interactive development
code --remote ssh-remote+mycluster /home/user/sky_workdir
# or: cursor --remote ssh-remote+mycluster /home/user/sky_workdir

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
  accelerators: H200:8
  # Optional: pin to a specific cloud/region/infra
  # infra: aws  # or aws/us-east-1, k8s, ssh/my-pool
  # If infra is left out, SkyPilot automatically fails over across all
  # enabled clouds/regions to find the cheapest available option.
  # Use spot instances for cost savings
  use_spot: false
  # Disk size in GB
  disk_size: 256
  # Open ports for serving
  ports: 8080

# Environment variables (accessible in file_mounts, setup, and run)
envs:
  MODEL_NAME: my-model
  BATCH_SIZE: 32

# Setup: runs once on cluster creation, cached on reuse
setup: |
  pip install torch transformers

# Run: the main command
run: |
  python train.py --model $MODEL_NAME --batch-size $BATCH_SIZE
```

For complete YAML schema including file mounts, environment variables set by SkyPilot, and advanced fields, see [YAML Specification](references/yaml-spec.md).

## GPU and Cloud Selection

**IMPORTANT: Let SkyPilot choose the cloud and region.** Do NOT manually pick a cloud/region/instance by parsing `sky gpus list` output. SkyPilot's optimizer automatically selects the cheapest available option across all enabled clouds. Only specify `infra:` when the user explicitly requests a specific cloud or region.

**Default behavior (recommended):** Just specify the GPU type. SkyPilot finds the cheapest cloud/region automatically:

```yaml
resources:
  accelerators: H200:8  # SkyPilot picks the cheapest cloud/region with H200:8
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

# Launch with autostop at launch time (preferred: saves cost, no follow-up command needed)
sky launch -c mycluster task.yaml -i 30        # stop after 30 min idle
sky launch -c mycluster task.yaml -i 30 --down # tear down after 30 min idle

# Override or pass environment variables via CLI
sky launch -c mycluster task.yaml --env MODEL_NAME=llama3 --env BATCH_SIZE=64

# Re-run a different task on the same cluster (fast, skips provisioning)
sky exec mycluster another_task.yaml

# Run an inline command
sky exec mycluster -- python train.py --epochs 10

# Set autostop after launch (use if you forgot to set -i at launch time)
sky autostop mycluster -i 30        # stop after 30 min idle, preserving disk (can restart with sky start)
sky autostop mycluster -i 30 --down # tear down after 30 min idle (disk is deleted, cannot restart)

# Stop to save costs, restart later
sky stop mycluster
sky start mycluster

# Tear down completely
sky down mycluster
```

## Managed Jobs

Use `sky jobs launch` for long-running jobs that should run unattended. SkyPilot manages the full lifecycle — provisioning, execution, recovery from preemptions/quota/failures, and teardown:

```yaml
# managed-job.yaml
name: training-job

resources:
  accelerators: A100:8

run: |
  python train.py --resume-from-checkpoint
```

```bash
# Launch as managed job
sky jobs launch managed-job.yaml

# Check status
sky jobs queue -o json

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

# Check status / get endpoint
sky serve status my-llm
sky serve status my-llm --endpoint

# Update (rolling)
sky serve update my-llm new-serve.yaml

# Tear down
sky serve down my-llm
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
3. Monitor with `sky jobs queue -o json`

### Model Serving Deployment
1. Write serve YAML with `service:` section
2. `sky serve up serve.yaml -n my-service`
3. Get endpoint: `sky serve status my-service --endpoint`
4. Update model: `sky serve update my-service updated.yaml`

## Agent Feedback Loop

When using SkyPilot programmatically, follow this loop:

1. **Validate**: `sky launch --dryrun task.yaml` (check resource availability/cost)
2. **Launch**: `sky launch -c mycluster task.yaml`
3. **Monitor**: `sky status -o json` and `sky queue mycluster -o json`
4. **Debug**: `sky logs mycluster` (stream logs) or `ssh mycluster` (interactive)
5. **Iterate**: `sky exec mycluster updated_task.yaml` (run on existing cluster)
6. **Cleanup**: `sky down mycluster`

## Common Agent Mistakes

| Mistake | Why it's wrong | Do this instead |
|---------|---------------|-----------------|
| Manually picking cloud/region from `sky gpus list` output | SkyPilot optimizer does this automatically and better | Just set `accelerators:` and let SkyPilot choose |
| Using `sky launch` for long-running unattended jobs | No recovery if preempted or interrupted | Use `sky jobs launch` for unattended work |
| Forgetting `sky down` or autostop after work is done | Wastes money on idle clusters | Always clean up, or use `-i <minutes> --down` at launch |
| Hardcoding `infra: aws` without user asking | Limits availability and increases cost | Only set `infra:` when user explicitly requests a cloud |
| Not using `envs:` for configurable values | Hard to reuse or override from CLI | Use `envs:` in YAML + `--env KEY=VAL` for parameterization |
| Running `sky launch` without `-c <name>` | Creates randomly-named cluster, hard to reference | Always name clusters with `-c` |
| Parsing table output from status commands | Table formatting is for humans, fragile to parse | Use `-o json` for structured output |
| Using deprecated `cloud:`/`region:`/`zone:` fields | Deprecated in favor of `infra:` | Use `infra: aws/us-east-1` instead |

## Common Issues Quick Reference

| Issue | Solution |
|-------|----------|
| GPU not available | Use `any_of` for fallback, or try different regions/clouds |
| Setup takes too long | SkyPilot caches setup; use `sky exec` to skip it on reruns |
| Task fails silently | Check `sky logs <cluster>` or `ssh <cluster>` to debug |
| Cluster stuck in INIT | `sky down <cluster>` and relaunch |
| Preemption/quota | Use `sky jobs launch` for automatic recovery and lifecycle management |
| Port not accessible | Ensure `ports:` is set in resources and security groups allow traffic |
| File sync slow | Use cloud bucket mounts instead of `workdir` for large datasets |
| Credentials error | Run `sky check -o json` and inspect which clouds are disabled |

## References

For detailed reference documentation:

- [CLI Reference](references/cli-reference.md) — All commands and flags
- [YAML Specification](references/yaml-spec.md) — Complete task YAML schema, file mounts, environment variables
- [Python SDK](references/python-sdk.md) — Programmatic API and SDK usage
- [Advanced Patterns](references/advanced-patterns.md) — Multi-cloud, distributed training, production patterns
- [Troubleshooting](references/troubleshooting.md) — Error diagnosis and solutions
- [Examples](references/examples.md) — Copy-paste task YAML examples
