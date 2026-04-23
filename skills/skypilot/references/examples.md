# SkyPilot Examples Reference

Copy-paste-ready YAML examples and CLI commands for common SkyPilot workflows.

---

## 1. Basic Examples

### Minimal Task (Just a Run Command)

The simplest possible SkyPilot task -- just a command to run.

```yaml
# minimal.yaml
name: hello-world

run: |
  echo "Hello from SkyPilot!"
  hostname
  uname -a
```

```bash
# Launch on any available cloud (cheapest option selected automatically)
sky launch -c hello minimal.yaml

# Tear down when done
sky down hello
```

### GPU Task with Specific Accelerator

Request a specific GPU type and count.

```yaml
# gpu-task.yaml
name: gpu-check

resources:
  # Request 1x A100 GPU
  accelerators: A100:1

setup: |
  pip install torch

run: |
  nvidia-smi
  python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}'); print(f'GPU: {torch.cuda.get_device_name(0)}')"
```

```bash
# Launch on any cloud that has A100
sky launch -c gpu-test gpu-task.yaml

# Or pin to a specific cloud
sky launch -c gpu-test gpu-task.yaml --infra aws

# Tear down
sky down gpu-test
```

### Task with Setup and Workdir

Sync local code and install dependencies before running.

```yaml
# train-task.yaml
name: my-training

# Sync current directory to ~/sky_workdir on the remote machine
workdir: .

resources:
  accelerators: L4:1

# Setup runs once on cluster creation, cached on subsequent runs
setup: |
  pip install torch torchvision
  pip install -r requirements.txt

# Run executes your main command
run: |
  python train.py --epochs 10
```

```bash
# Launch -- setup runs first, then the run command
sky launch -c train train-task.yaml

# Re-run with different args (setup is cached, only run executes)
sky exec train train-task.yaml
```

### Task with Environment Variables and Secrets

Pass configuration via envs and sensitive values via secrets.

```yaml
# env-task.yaml
name: training-with-config

resources:
  accelerators: A100:1

# Environment variables -- visible in logs and dashboard
envs:
  MODEL_NAME: bert-base-uncased
  BATCH_SIZE: "32"
  LEARNING_RATE: "1e-4"

# Secrets -- redacted in logs and dashboard
secrets:
  HF_TOKEN: null        # Will be passed via CLI
  WANDB_API_KEY: null    # Will be passed via CLI

setup: |
  pip install transformers datasets wandb

run: |
  echo "Training model: $MODEL_NAME with batch size $BATCH_SIZE"
  wandb login $WANDB_API_KEY
  python train.py \
    --model $MODEL_NAME \
    --batch-size $BATCH_SIZE \
    --lr $LEARNING_RATE
```

```bash
# Pass secrets and override envs from the CLI
HF_TOKEN=hf_abc123 WANDB_API_KEY=wk_xyz789 \
  sky launch -c train env-task.yaml \
    --secret HF_TOKEN --secret WANDB_API_KEY \
    --env BATCH_SIZE=64

# Secrets can also be specified inline
sky launch -c train env-task.yaml \
  --secret HF_TOKEN=hf_abc123 \
  --secret WANDB_API_KEY=wk_xyz789
```

### Inline Command (No YAML File)

Run a quick command without writing a YAML file.

```bash
# Launch a GPU instance and run nvidia-smi
sky launch -c quick --gpus H100 -- nvidia-smi

# Run a Python script on an existing cluster
sky exec quick -- python -c "import torch; print(torch.cuda.device_count())"

# Launch with specific cloud and region
sky launch -c quick --gpus A100 --infra gcp/us-central1 -- nvidia-smi
```

### Multi-Node Task

Launch a task across multiple nodes.

```yaml
# multi-node.yaml
name: multi-node-check

# Launch 2 nodes (1 head + 1 worker)
num_nodes: 2

resources:
  accelerators: L4:1

run: |
  echo "Node rank: $SKYPILOT_NODE_RANK"
  echo "Total nodes: $SKYPILOT_NUM_NODES"
  echo "All node IPs:"
  echo "$SKYPILOT_NODE_IPS"
  echo "GPUs per node: $SKYPILOT_NUM_GPUS_PER_NODE"
  nvidia-smi
```

```bash
sky launch -c multi multi-node.yaml
```

---

## 2. Training Examples

### Single-GPU PyTorch Training

A complete single-GPU training example with local code sync.

```yaml
# single-gpu-train.yaml
name: pytorch-single-gpu

workdir: .

resources:
  accelerators: A100:1
  disk_size: 256

envs:
  NUM_EPOCHS: "10"
  BATCH_SIZE: "32"

setup: |
  pip install torch torchvision
  pip install wandb tqdm

run: |
  python train.py \
    --epochs $NUM_EPOCHS \
    --batch-size $BATCH_SIZE \
    --output-dir /tmp/checkpoints
```

```bash
sky launch -c train single-gpu-train.yaml
sky logs train      # Stream logs
sky down train      # Tear down when done
```

### Multi-GPU Single-Node Training

Use all GPUs on a single node with torchrun.

```yaml
# multi-gpu-train.yaml
name: pytorch-multi-gpu

workdir: .

resources:
  # 4x A100 GPUs on a single node
  accelerators: A100:4
  disk_size: 512

envs:
  NUM_EPOCHS: "10"
  BATCH_SIZE: "64"

setup: |
  pip install torch torchvision
  pip install wandb tqdm

run: |
  torchrun \
    --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE \
    --master_port=12345 \
    train.py \
      --epochs $NUM_EPOCHS \
      --batch-size $BATCH_SIZE \
      --output-dir /tmp/checkpoints
```

```bash
sky launch -c multi-gpu multi-gpu-train.yaml
```

### Multi-Node Distributed Training (PyTorch DDP)

Distributed training across 2 nodes with 8 GPUs each using torchrun.

```yaml
# multi-node-ddp.yaml
name: pytorch-ddp-training

workdir: .

# 2 nodes, each with 8 GPUs = 16 GPUs total
num_nodes: 2

resources:
  accelerators: A100-80GB:8
  disk_size: 512

envs:
  NUM_EPOCHS: "20"
  BATCH_SIZE: "256"

setup: |
  pip install torch torchvision
  pip install wandb tqdm

run: |
  # SKYPILOT_NODE_IPS contains all node IPs, one per line
  # The first IP is always the head node (rank 0)
  MASTER_ADDR=$(echo "$SKYPILOT_NODE_IPS" | head -n1)

  torchrun \
    --nnodes=$SKYPILOT_NUM_NODES \
    --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE \
    --master_addr=$MASTER_ADDR \
    --master_port=12345 \
    --node_rank=$SKYPILOT_NODE_RANK \
    train.py \
      --epochs $NUM_EPOCHS \
      --batch-size $BATCH_SIZE \
      --output-dir /tmp/checkpoints
```

```bash
sky launch -c ddp-train multi-node-ddp.yaml

# SSH into the head node to check progress
ssh ddp-train
```

### Training with Checkpointing to Cloud Storage

Save checkpoints to S3/GCS for persistence across runs.

```yaml
# train-with-checkpoints.yaml
name: training-with-checkpoints

workdir: .

resources:
  accelerators: A100:4
  disk_size: 512

envs:
  CHECKPOINT_BUCKET: my-training-checkpoints

file_mounts:
  # Mount an S3 bucket for writing checkpoints
  /checkpoints:
    name: ${CHECKPOINT_BUCKET}
    store: s3
    mode: MOUNT

  # Mount a dataset bucket (read-only streaming)
  /data:
    source: s3://my-training-data/imagenet
    mode: MOUNT

setup: |
  pip install torch torchvision boto3

run: |
  # Training script should save checkpoints to /checkpoints
  # and load dataset from /data
  torchrun \
    --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE \
    train.py \
      --data-dir /data \
      --checkpoint-dir /checkpoints \
      --resume-from-checkpoint
```

```bash
# Launch training
sky launch -c train train-with-checkpoints.yaml

# Re-launch after preemption or failure -- resumes from last checkpoint
sky launch -c train train-with-checkpoints.yaml
```

### Training on Spot Instances with Auto-Recovery (Managed Job)

Run long training on cheap spot instances with automatic recovery from preemptions.

```yaml
# spot-train.yaml
name: spot-training-resnet

workdir: .

resources:
  accelerators: A100:4
  # Use spot instances for ~3x cost savings
  use_spot: true
  disk_size: 512

  # Recovery strategy when spot instance is preempted
  job_recovery:
    strategy: EAGER_NEXT_REGION    # Move to next region on preemption
    max_restarts_on_errors: 3      # Retry up to 3 times on user code errors

envs:
  CHECKPOINT_BUCKET: my-spot-training-ckpts

file_mounts:
  # Persistent checkpoint storage -- survives preemptions
  /checkpoints:
    name: ${CHECKPOINT_BUCKET}
    store: s3
    mode: MOUNT

setup: |
  pip install torch torchvision

run: |
  # IMPORTANT: Your training script must handle checkpoint resume.
  # SkyPilot recovers the cluster; your script recovers the state.
  torchrun \
    --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE \
    train.py \
      --checkpoint-dir /checkpoints \
      --resume-from-checkpoint
```

```bash
# Launch as a managed job (NOT sky launch -- use sky jobs launch)
sky jobs launch spot-train.yaml

# Monitor the job
sky jobs queue

# Stream logs
sky jobs logs <job_id>
```

### Fine-Tuning a HuggingFace Model

Fine-tune a HuggingFace model with LoRA, using secrets for authentication.

```yaml
# finetune-hf.yaml
name: llama3-finetune

workdir: .

resources:
  accelerators: A100-80GB:4
  disk_size: 512
  disk_tier: best

envs:
  MODEL_ID: meta-llama/Meta-Llama-3.1-8B-Instruct
  DATASET: yahma/alpaca-cleaned
  CHECKPOINT_BUCKET: my-finetune-checkpoints

# HuggingFace token is a secret -- redacted in logs/dashboard
secrets:
  HF_TOKEN: null  # Pass via CLI: --secret HF_TOKEN=hf_xxx

file_mounts:
  /output:
    name: ${CHECKPOINT_BUCKET}
    mode: MOUNT

setup: |
  pip install torch==2.4.0 torchvision
  pip install transformers peft trl datasets accelerate
  pip install bitsandbytes

  # Download the model (needs HF_TOKEN for gated models)
  huggingface-cli download $MODEL_ID \
    --token $HF_TOKEN \
    --local-dir /tmp/model

run: |
  python finetune_lora.py \
    --model-path /tmp/model \
    --dataset $DATASET \
    --output-dir /output/lora-adapter \
    --num-epochs 3 \
    --batch-size 4 \
    --learning-rate 2e-4
```

```bash
# Launch with HF_TOKEN secret
HF_TOKEN=hf_abc123 sky launch -c finetune finetune-hf.yaml --secret HF_TOKEN
```

---

## 3. Model Serving Examples

### Basic vLLM Serving (Single GPU)

Serve a model with vLLM behind a SkyServe endpoint.

```yaml
# vllm-serve.yaml
name: vllm-llama

resources:
  accelerators: A100:1
  ports: 8000

envs:
  MODEL_NAME: meta-llama/Llama-3.1-8B-Instruct

secrets:
  HF_TOKEN: null

setup: |
  pip install vllm transformers
  huggingface-cli login --token $HF_TOKEN

run: |
  python -m vllm.entrypoints.openai.api_server \
    --model $MODEL_NAME \
    --host 0.0.0.0 \
    --port 8000

# SkyServe configuration
service:
  # Readiness check endpoint (must return 200 when ready)
  readiness_probe: /v1/models
  # Fixed number of replicas
  replicas: 1
```

```bash
# Deploy as a service
HF_TOKEN=hf_xxx sky serve up vllm-serve.yaml -n llama-svc --secret HF_TOKEN

# Get the endpoint URL
sky serve status llama-svc --endpoint

# Test the endpoint
ENDPOINT=$(sky serve status llama-svc --endpoint)
curl $ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'

# Tear down
sky serve down llama-svc
```

### vLLM with Autoscaling

Scale replicas automatically based on query load.

```yaml
# vllm-autoscale.yaml
name: vllm-autoscaling

resources:
  accelerators: A100:1
  ports: 8000

envs:
  MODEL_NAME: meta-llama/Llama-3.1-8B-Instruct

secrets:
  HF_TOKEN: null

setup: |
  pip install vllm transformers
  huggingface-cli login --token $HF_TOKEN

run: |
  python -m vllm.entrypoints.openai.api_server \
    --model $MODEL_NAME \
    --host 0.0.0.0 \
    --port 8000

service:
  readiness_probe:
    path: /v1/models
    # Wait 20 minutes for model loading before checking readiness
    initial_delay_seconds: 1200
  replica_policy:
    min_replicas: 1
    max_replicas: 5
    # Scale up when each replica handles more than 5 QPS
    target_qps_per_replica: 5
    # Wait 5 minutes before adding replicas
    upscale_delay_seconds: 300
    # Wait 20 minutes before removing replicas
    downscale_delay_seconds: 1200
```

```bash
HF_TOKEN=hf_xxx sky serve up vllm-autoscale.yaml -n llama-auto --secret HF_TOKEN
```

### Serving on Spot Instances

Use spot instances for serving replicas to reduce cost.

```yaml
# vllm-spot-serve.yaml
name: vllm-spot-serving

resources:
  accelerators: A100:1
  ports: 8000
  # Use spot instances for replicas -- SkyServe handles recovery
  use_spot: true

envs:
  MODEL_NAME: meta-llama/Llama-3.1-8B-Instruct

secrets:
  HF_TOKEN: null

setup: |
  pip install vllm transformers
  huggingface-cli login --token $HF_TOKEN

run: |
  python -m vllm.entrypoints.openai.api_server \
    --model $MODEL_NAME \
    --host 0.0.0.0 \
    --port 8000

service:
  readiness_probe: /v1/models
  replica_policy:
    # Keep at least 2 replicas for high availability with spot
    min_replicas: 2
    max_replicas: 6
    target_qps_per_replica: 5
```

```bash
HF_TOKEN=hf_xxx sky serve up vllm-spot-serve.yaml -n llama-spot --secret HF_TOKEN
```

### Multi-Model Serving

Serve multiple models by deploying separate services.

```yaml
# serve-llama-70b.yaml
name: llama-70b-service

resources:
  # 70B model needs multi-GPU with tensor parallelism
  accelerators: A100-80GB:4
  ports: 8000

envs:
  MODEL_NAME: meta-llama/Llama-3.1-70B-Instruct

secrets:
  HF_TOKEN: null

setup: |
  pip install vllm transformers
  huggingface-cli login --token $HF_TOKEN

run: |
  python -m vllm.entrypoints.openai.api_server \
    --model $MODEL_NAME \
    --tensor-parallel-size 4 \
    --host 0.0.0.0 \
    --port 8000

service:
  readiness_probe: /v1/models
  replicas: 2
```

```bash
# Deploy multiple model services
HF_TOKEN=hf_xxx sky serve up vllm-serve.yaml -n llama-8b --secret HF_TOKEN
HF_TOKEN=hf_xxx sky serve up serve-llama-70b.yaml -n llama-70b --secret HF_TOKEN

# Check all services
sky serve status

# Get endpoints
sky serve status llama-8b --endpoint
sky serve status llama-70b --endpoint
```

### TGI (Text Generation Inference) Example

Serve a model using HuggingFace TGI with Docker.

```yaml
# tgi-serve.yaml
name: tgi-service

resources:
  accelerators: A100:1
  ports: 8080

envs:
  MODEL_ID: meta-llama/Llama-3.1-8B-Instruct

secrets:
  HF_TOKEN: null

run: |
  docker run --gpus all --shm-size 1g \
    -p 8080:80 \
    -e HUGGING_FACE_HUB_TOKEN=$HF_TOKEN \
    -v ~/data:/data \
    ghcr.io/huggingface/text-generation-inference \
    --model-id $MODEL_ID

service:
  readiness_probe: /health
  replicas: 2
```

```bash
HF_TOKEN=hf_xxx sky serve up tgi-serve.yaml -n tgi-llama --secret HF_TOKEN
sky serve status tgi-llama --endpoint
```

### Service with Custom Readiness Probe (POST)

Use a POST request with custom data for the readiness check.

```yaml
# custom-probe-serve.yaml
name: custom-model-service

resources:
  accelerators: A100:1
  ports: 8080

setup: |
  pip install my-model-server

run: |
  python -m my_model_server --port 8080

service:
  readiness_probe:
    path: /v1/predict
    # Use POST with custom payload for readiness checking
    post_data: '{"text": "health check", "max_tokens": 1}'
    # Wait 30 minutes for model loading
    initial_delay_seconds: 1800
    # Timeout for each probe request
    timeout_seconds: 30
  replica_policy:
    min_replicas: 1
    max_replicas: 3
    target_qps_per_replica: 10
```

```bash
sky serve up custom-probe-serve.yaml -n custom-svc
```

---

## 4. File Mounts and Storage Examples

### Sync Local Directory (workdir)

The workdir syncs your local directory to `~/sky_workdir` on the remote cluster.

```yaml
# workdir-example.yaml
name: local-code-sync

# Sync current directory to ~/sky_workdir
workdir: .

resources:
  accelerators: L4:1

setup: |
  pip install -r requirements.txt

run: |
  # Commands run under ~/sky_workdir
  python train.py
```

```bash
# Any changes to local files are synced on each sky launch/exec
sky launch -c dev workdir-example.yaml

# Re-sync and re-run (setup is cached)
sky exec dev workdir-example.yaml
```

### Mount S3 Bucket (mode: MOUNT)

Stream data from S3 without downloading -- read-only, low disk usage.

```yaml
# s3-mount.yaml
name: s3-streaming

resources:
  accelerators: A100:1

file_mounts:
  # MOUNT mode: read-only, streams data on access, no local copy
  /data/imagenet:
    source: s3://my-imagenet-bucket/train
    mode: MOUNT

run: |
  python train.py --data-dir /data/imagenet
```

```bash
sky launch -c train s3-mount.yaml
```

### Mount with Cache (mode: MOUNT_CACHED)

Cache accessed data locally for fast random access -- ideal for model weights.

```yaml
# mount-cached.yaml
name: cached-model-weights

resources:
  accelerators: A100:1
  disk_size: 512  # Need enough disk for cached data

file_mounts:
  # MOUNT_CACHED: caches accessed files locally for fast random access
  # Good for model weights that are read repeatedly
  /models/llama:
    source: s3://my-model-weights/llama-3.1-8b
    mode: MOUNT_CACHED

run: |
  python serve.py --model-path /models/llama
```

```bash
sky launch -c serve mount-cached.yaml
```

### Copy from Cloud Storage (mode: COPY)

Download data fully -- read-write access, full local copy.

```yaml
# copy-mount.yaml
name: full-copy

resources:
  accelerators: A100:1
  # Need enough disk for the full dataset
  disk_size: 1024

file_mounts:
  # COPY mode: full download, read-write, supports modification
  /data/dataset:
    source: s3://my-dataset-bucket/training-data
    mode: COPY

run: |
  # Can read AND write to the copied data
  python preprocess.py --input /data/dataset --output /data/dataset/processed
  python train.py --data-dir /data/dataset/processed
```

```bash
sky launch -c train copy-mount.yaml
```

### Mount GCS Bucket

Mount a Google Cloud Storage bucket.

```yaml
# gcs-mount.yaml
name: gcs-data

resources:
  infra: gcp
  accelerators: A100:1

file_mounts:
  # GCS bucket mount
  /data:
    source: gs://my-gcs-bucket/datasets
    mode: MOUNT

  # Create a new GCS bucket for output
  /output:
    name: my-training-output-bucket
    store: gcs
    mode: MOUNT

run: |
  python train.py --data /data --output /output
```

```bash
sky launch -c gcp-train gcs-mount.yaml
```

### Using Volumes on Kubernetes

Kubernetes persistent and ephemeral volumes.

```yaml
# k8s-volumes.yaml
name: k8s-with-volumes

resources:
  infra: k8s
  accelerators: A100:1

# Volumes (Kubernetes only)
volumes:
  # Persistent volume -- data survives cluster restarts
  /mnt/data: my-persistent-volume

  # Ephemeral volume -- fast scratch space, lost on restart
  /mnt/scratch:
    size: 200Gi

run: |
  # /mnt/data persists across restarts
  # /mnt/scratch is fast ephemeral storage
  python train.py \
    --data-dir /mnt/data \
    --cache-dir /mnt/scratch
```

```bash
sky launch -c k8s-train k8s-volumes.yaml
```

### Multiple Mounts in One Task

Combine different mount types in a single task.

```yaml
# multi-mount.yaml
name: multi-mount-training

workdir: .

resources:
  accelerators: A100:4
  disk_size: 512

envs:
  OUTPUT_BUCKET: my-training-outputs

file_mounts:
  # Sync local config files
  /configs: ./configs

  # Stream training data from S3 (read-only)
  /data/train:
    source: s3://my-dataset/train
    mode: MOUNT

  # Cache model weights locally for fast loading
  /models/pretrained:
    source: s3://my-models/llama-3.1-8b
    mode: MOUNT_CACHED

  # Writable output bucket for checkpoints
  /output:
    name: ${OUTPUT_BUCKET}
    store: s3
    mode: MOUNT

  # Copy evaluation data (small, needs read-write)
  /data/eval:
    source: s3://my-dataset/eval
    mode: COPY

setup: |
  pip install torch transformers datasets

run: |
  python train.py \
    --config /configs/train_config.yaml \
    --data-dir /data/train \
    --eval-dir /data/eval \
    --pretrained-model /models/pretrained \
    --output-dir /output
```

```bash
sky launch -c train multi-mount.yaml
```

---

## 5. Multi-Cloud and Fallback Examples

### any_of: Multiple GPU Types

Let SkyPilot pick the cheapest available GPU from a set.

```yaml
# any-gpu.yaml
name: flexible-gpu-training

resources:
  # SkyPilot picks the cheapest available option
  any_of:
    - accelerators: H100:1
    - accelerators: A100-80GB:1
    - accelerators: A100:1
    - accelerators: L40S:1

run: |
  nvidia-smi
  python train.py
```

```bash
sky launch -c train any-gpu.yaml
```

### any_of: Multiple Clouds for Same GPU

Try different clouds for the same GPU type.

```yaml
# any-cloud.yaml
name: multi-cloud-a100

resources:
  accelerators: A100-80GB:8
  any_of:
    - infra: aws/us-east-1
    - infra: gcp/us-central1
    - infra: azure/eastus

run: |
  nvidia-smi
  python train.py
```

```bash
sky launch -c train any-cloud.yaml
```

### ordered: Strict Priority

Try clouds/regions in a specific order -- first match wins.

```yaml
# ordered-priority.yaml
name: priority-training

resources:
  ordered:
    # Try AWS us-east-1 first (our reserved instances)
    - infra: aws/us-east-1
      accelerators: H100:8
    # Then try GCP us-central1
    - infra: gcp/us-central1
      accelerators: H100:8
    # Fall back to A100s on any cloud
    - accelerators: A100-80GB:8

run: |
  python train.py
```

```bash
sky launch -c train ordered-priority.yaml
```

### K8s + Cloud Fallback

Try Kubernetes first, fall back to cloud if K8s has no capacity.

```yaml
# k8s-fallback.yaml
name: k8s-or-cloud

resources:
  accelerators: A100:1
  ordered:
    # Try on-prem K8s cluster first (free)
    - infra: k8s/my-onprem-cluster
    # Fall back to AWS
    - infra: aws
    # Fall back to GCP
    - infra: gcp

run: |
  python train.py
```

```bash
sky launch -c train k8s-fallback.yaml
```

### Region Preference with Wildcards

Use wildcards to match multiple regions or zones.

```yaml
# wildcard-region.yaml
name: us-region-only

resources:
  accelerators: A100:8
  any_of:
    # Any zone in us-east-1
    - infra: aws/us-east-1
    # Any zone in us-west-2
    - infra: aws/us-west-2
    # Any US region on GCP
    - infra: gcp/us-*

run: |
  python train.py
```

```bash
sky launch -c train wildcard-region.yaml
```

### Cost Optimization: Cheapest Across All Clouds

Omit `infra` to let SkyPilot search all enabled clouds for the cheapest option.

```yaml
# cheapest.yaml
name: cheapest-option

resources:
  # No infra specified -- SkyPilot searches all enabled clouds
  accelerators: A100:1

run: |
  python train.py
```

```bash
# Dry run to see the cheapest option without launching
sky launch cheapest.yaml --dryrun

# Launch on the cheapest cloud/region
sky launch -c train cheapest.yaml

# Check GPU prices across clouds
sky gpus list A100
```

---

## 6. Managed Job Examples

### Basic Managed Job

Managed jobs run on a dedicated controller that monitors and manages your job.

```yaml
# basic-job.yaml
name: data-processing

resources:
  cpus: 16+
  memory: 64+

setup: |
  pip install pandas pyarrow

run: |
  python process_data.py --output /tmp/results
```

```bash
# Launch as a managed job (runs on a separate controller)
sky jobs launch basic-job.yaml

# Check status
sky jobs queue

# Stream logs
sky jobs logs 1

# Cancel the job
sky jobs cancel 1
```

### Spot Job with FAILOVER Recovery

Automatically retry in the same region first, then try other regions.

```yaml
# spot-failover.yaml
name: training-failover

resources:
  accelerators: A100:4
  use_spot: true
  job_recovery:
    # FAILOVER: retry same region first, then move to next region
    strategy: FAILOVER
    # Retry up to 3 times on user code errors
    max_restarts_on_errors: 3

file_mounts:
  /checkpoints:
    name: my-training-ckpts
    store: s3
    mode: MOUNT

setup: |
  pip install torch transformers

run: |
  python train.py --checkpoint-dir /checkpoints --resume
```

```bash
sky jobs launch spot-failover.yaml
```

### Spot Job with EAGER_NEXT_REGION Recovery

Move to a different region immediately on preemption -- good for spot shortages.

```yaml
# spot-eager.yaml
name: training-eager-recovery

resources:
  accelerators: A100:8
  use_spot: true
  job_recovery:
    # EAGER_NEXT_REGION: immediately try a different region on preemption
    # Best for spot instances where preemption signals regional shortage
    strategy: EAGER_NEXT_REGION

file_mounts:
  /checkpoints:
    name: my-training-ckpts
    store: s3
    mode: MOUNT

setup: |
  pip install torch transformers

run: |
  python train.py --checkpoint-dir /checkpoints --resume
```

```bash
sky jobs launch spot-eager.yaml
```

### Hyperparameter Sweep

Launch multiple managed jobs with different hyperparameters.

```yaml
# sweep.yaml
name: hp-sweep

resources:
  accelerators: A100:1

envs:
  LR: "1e-4"
  WEIGHT_DECAY: "0.01"
  BATCH_SIZE: "32"

file_mounts:
  /results:
    name: sweep-results
    store: s3
    mode: MOUNT

setup: |
  pip install torch transformers wandb

run: |
  python train.py \
    --lr $LR \
    --weight-decay $WEIGHT_DECAY \
    --batch-size $BATCH_SIZE \
    --output-dir /results/lr-${LR}_wd-${WEIGHT_DECAY}_bs-${BATCH_SIZE}
```

```bash
# Launch multiple jobs with different hyperparameters
for lr in 1e-3 1e-4 1e-5; do
  for wd in 0.01 0.1; do
    sky jobs launch sweep.yaml \
      --env LR=$lr --env WEIGHT_DECAY=$wd \
      --name sweep-lr${lr}-wd${wd}
  done
done

# Monitor all sweep jobs
sky jobs queue
```

### Job with max_restarts_on_errors

Automatically restart on user code errors (non-zero exit codes).

```yaml
# auto-restart.yaml
name: resilient-training

resources:
  accelerators: A100:4
  use_spot: true
  job_recovery:
    strategy: FAILOVER
    # Restart up to 5 times if the training script crashes
    max_restarts_on_errors: 5

file_mounts:
  /checkpoints:
    name: my-ckpts
    store: s3
    mode: MOUNT

setup: |
  pip install torch transformers

run: |
  python train.py --checkpoint-dir /checkpoints --resume
```

```bash
sky jobs launch auto-restart.yaml
```

### Job with recover_on_exit_codes for NCCL Errors

Always retry on specific exit codes (such as NCCL timeouts).

```yaml
# nccl-recovery.yaml
name: nccl-resilient-training

num_nodes: 4

resources:
  accelerators: H100:8
  use_spot: true
  job_recovery:
    strategy: EAGER_NEXT_REGION
    max_restarts_on_errors: 3
    # Exit code 33 = NCCL timeout (convention in many training scripts)
    # These restarts do NOT count toward max_restarts_on_errors
    recover_on_exit_codes: [33, 34]

file_mounts:
  /checkpoints:
    name: my-distributed-ckpts
    store: s3
    mode: MOUNT

setup: |
  pip install torch transformers

run: |
  MASTER_ADDR=$(echo "$SKYPILOT_NODE_IPS" | head -n1)
  torchrun \
    --nnodes=$SKYPILOT_NUM_NODES \
    --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE \
    --master_addr=$MASTER_ADDR \
    --master_port=12345 \
    --node_rank=$SKYPILOT_NODE_RANK \
    train.py --checkpoint-dir /checkpoints --resume
```

```bash
sky jobs launch nccl-recovery.yaml
```

### Job Pool Example

Use a job pool for batch processing with pre-provisioned workers.

```yaml
# pool.yaml
# Pool configuration: pre-provisions workers with setup
workdir: .

resources:
  accelerators: H100:1
  disk_size: 100

envs:
  MODEL_NAME: meta-llama/Llama-3.1-8B

setup: |
  pip install vllm transformers datasets
  # Pre-download model so all jobs can use it immediately
  huggingface-cli download $MODEL_NAME --local-dir /tmp/model

pool:
  workers: 3
```

```yaml
# job.yaml
# Job that runs on the pool
name: batch-inference

resources:
  accelerators: H100:1

run: |
  echo "Processing partition ${SKYPILOT_JOB_RANK} of ${SKYPILOT_NUM_JOBS}"
  python classify.py \
    --job-rank ${SKYPILOT_JOB_RANK} \
    --num-jobs ${SKYPILOT_NUM_JOBS} \
    --model-path /tmp/model \
    --output-dir /results
```

```bash
# Create the pool with 3 workers
sky jobs pool apply -p my-pool pool.yaml

# Submit 10 jobs to the pool (distributed across 3 workers)
sky jobs launch -p my-pool --num-jobs 10 job.yaml

# Check pool status
sky jobs pool status my-pool

# Check job status
sky jobs queue

# Tear down pool when done
sky jobs pool down my-pool
```

---

## 7. Distributed Training Examples

### PyTorch DDP on 4 Nodes (Complete Example)

Full distributed training with torchrun and NCCL configuration.

```yaml
# ddp-4node.yaml
name: ddp-4node-training

workdir: .

num_nodes: 4

resources:
  accelerators: H100:8
  disk_size: 1024

envs:
  # NCCL configuration for optimal multi-node communication
  NCCL_DEBUG: INFO
  NCCL_SOCKET_IFNAME: eth0
  # Training hyperparameters
  GLOBAL_BATCH_SIZE: "512"
  NUM_EPOCHS: "50"
  CHECKPOINT_BUCKET: my-ddp-checkpoints

file_mounts:
  # Training data (streaming)
  /data:
    source: s3://my-training-data/preprocessed
    mode: MOUNT

  # Checkpoint storage (persistent, writable)
  /checkpoints:
    name: ${CHECKPOINT_BUCKET}
    store: s3
    mode: MOUNT

setup: |
  pip install torch torchvision
  pip install wandb tensorboard

run: |
  MASTER_ADDR=$(echo "$SKYPILOT_NODE_IPS" | head -n1)
  NUM_GPUS=$(($SKYPILOT_NUM_GPUS_PER_NODE * $SKYPILOT_NUM_NODES))

  echo "Node rank: $SKYPILOT_NODE_RANK / $SKYPILOT_NUM_NODES"
  echo "Master: $MASTER_ADDR"
  echo "Total GPUs: $NUM_GPUS"

  torchrun \
    --nnodes=$SKYPILOT_NUM_NODES \
    --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE \
    --master_addr=$MASTER_ADDR \
    --master_port=29500 \
    --node_rank=$SKYPILOT_NODE_RANK \
    train_ddp.py \
      --data-dir /data \
      --checkpoint-dir /checkpoints \
      --global-batch-size $GLOBAL_BATCH_SIZE \
      --epochs $NUM_EPOCHS \
      --resume
```

```bash
# Launch distributed training
sky launch -c ddp4 ddp-4node.yaml

# SSH into head node
ssh ddp4

# Check worker node
ssh ddp4-worker1

# Monitor logs
sky logs ddp4
```

### DeepSpeed ZeRO-3 on 2 Nodes

Distributed training with DeepSpeed, including hostfile generation.

```yaml
# deepspeed-2node.yaml
name: deepspeed-zero3

num_nodes: 2

resources:
  accelerators: A100-80GB:8
  disk_size: 1024
  image_id: docker:nvidia/cuda:12.1.1-devel-ubuntu20.04

envs:
  MODEL_NAME: meta-llama/Llama-3.1-8B
  DEEPSPEED_ENVS: "MODEL_NAME,SKYPILOT_NODE_RANK"

setup: |
  pip install torch==2.4.0 --index-url https://download.pytorch.org/whl/cu121
  pip install deepspeed==0.14.4
  pip install transformers datasets accelerate
  sudo apt-get update && sudo apt-get -y install pdsh

run: |
  # Only the head node (rank 0) launches DeepSpeed
  if [ "$SKYPILOT_NODE_RANK" == "0" ]; then
    # Generate hostfile for DeepSpeed
    HOSTFILE=/tmp/hostfile
    python -c "
  import os
  n_gpus = os.environ['SKYPILOT_NUM_GPUS_PER_NODE']
  ips = os.environ['SKYPILOT_NODE_IPS'].splitlines()
  with open('$HOSTFILE', 'w') as f:
      for ip in ips:
          f.write(f'{ip} slots={n_gpus}\n')
  "
    echo "Hostfile:"
    cat $HOSTFILE

    # Generate .deepspeed_env for environment propagation
    python -c "
  import os
  envs = os.environ.get('DEEPSPEED_ENVS', '').split(',')
  with open('.deepspeed_env', 'w') as f:
      for var in envs:
          f.write(f'{var}=\"{os.getenv(var, \"\")}\"\n')
  "

    deepspeed \
      --hostfile $HOSTFILE \
      train_deepspeed.py \
        --model-name $MODEL_NAME \
        --deepspeed \
        --deepspeed-config ds_config_zero3.json
  fi
```

```bash
sky launch -c ds2 deepspeed-2node.yaml
```

### FSDP Training Example

Multi-node Fully Sharded Data Parallel training with HuggingFace Accelerate.

```yaml
# fsdp-train.yaml
name: fsdp-training

workdir: .

num_nodes: 2

resources:
  accelerators: H100:8
  disk_size: 1024

envs:
  MODEL_ID: meta-llama/Llama-3.1-8B
  CHECKPOINT_BUCKET: my-fsdp-checkpoints

secrets:
  HF_TOKEN: null

file_mounts:
  /output:
    name: ${CHECKPOINT_BUCKET}
    store: s3
    mode: MOUNT

setup: |
  pip install torch --index-url https://download.pytorch.org/whl/cu128
  pip install transformers accelerate trl peft datasets
  pip install deepspeed  # Needed for some FSDP optimizations

run: |
  MASTER_ADDR=$(echo "$SKYPILOT_NODE_IPS" | head -n1)
  NUM_PROCESSES=$(($SKYPILOT_NUM_GPUS_PER_NODE * $SKYPILOT_NUM_NODES))

  accelerate launch \
    --config_file configs/fsdp_config.yaml \
    --num_machines $SKYPILOT_NUM_NODES \
    --num_processes $NUM_PROCESSES \
    --machine_rank $SKYPILOT_NODE_RANK \
    --main_process_ip $MASTER_ADDR \
    --main_process_port 29500 \
    train_fsdp.py \
      --model-id $MODEL_ID \
      --output-dir /output \
      --resume-from-checkpoint
```

```bash
HF_TOKEN=hf_xxx sky launch -c fsdp fsdp-train.yaml --secret HF_TOKEN
```

### Ray Train Multi-Node Example

Distributed training using Ray Train across multiple nodes.

```yaml
# ray-train.yaml
name: ray-train-distributed

workdir: .

num_nodes: 2

resources:
  accelerators: A100:4

setup: |
  pip install "ray[train]" torch torchvision
  pip install transformers datasets

run: |
  # SkyPilot sets up a Ray cluster across all nodes automatically.
  # The head node's Ray address is available at the standard port.
  if [ "$SKYPILOT_NODE_RANK" == "0" ]; then
    # Only run training script on the head node;
    # Ray distributes the work to all nodes
    python train_ray.py \
      --num-workers $((SKYPILOT_NUM_NODES * SKYPILOT_NUM_GPUS_PER_NODE)) \
      --gpus-per-worker 1
  fi
```

The `train_ray.py` script uses `ray.train`:

```python
# train_ray.py (referenced by the YAML above)
import ray
from ray import train
from ray.train.torch import TorchTrainer
from ray.train import ScalingConfig

def train_func(config):
    import torch
    # Your PyTorch training code here
    # ray.train handles distributed setup automatically
    model = ...
    optimizer = ...
    for epoch in range(config["epochs"]):
        train_epoch(model, optimizer)
        # Report metrics
        train.report({"loss": loss, "epoch": epoch})

scaling_config = ScalingConfig(
    num_workers=args.num_workers,
    use_gpu=True,
    resources_per_worker={"GPU": args.gpus_per_worker},
)

trainer = TorchTrainer(
    train_func,
    train_loop_config={"epochs": 10},
    scaling_config=scaling_config,
)
result = trainer.fit()
```

```bash
sky launch -c ray-train ray-train.yaml
```

---

## 8. Python SDK Examples

### Basic: Create Task, Set Resources, Launch

```python
import sky

# Create a task programmatically
task = sky.Task(
    name='hello-gpu',
    run='nvidia-smi && python -c "import torch; print(torch.cuda.device_count())"',
)
task.set_resources(sky.Resources(
    accelerators='A100:1',
))

# Launch returns a request ID (async by default)
request_id = sky.launch(task, cluster_name='my-cluster')

# Wait for the launch to complete
job_id, handle = sky.get(request_id)
print(f'Job ID: {job_id}')
```

### Launch from YAML File

```python
import sky

# Load task from a YAML file
task = sky.Task.from_yaml('train.yaml')

# Launch with options
request_id = sky.launch(
    task,
    cluster_name='train-cluster',
    idle_minutes_to_autostop=30,  # Auto-stop after 30 min idle
    retry_until_up=True,          # Keep trying if resources unavailable
)
result = sky.get(request_id)
```

### Check Status and Get Cluster Info

```python
import sky

# Get all cluster statuses
request_id = sky.status()
clusters = sky.get(request_id)

for cluster in clusters:
    print(f"Cluster: {cluster['name']}")
    print(f"  Status: {cluster['status']}")
    print(f"  Resources: {cluster['resources_str']}")
    print(f"  Autostop: {cluster['autostop']} min")

# Get status for a specific cluster
request_id = sky.status(cluster_names=['my-cluster'])
clusters = sky.get(request_id)

# Get job queue for a cluster
request_id = sky.queue('my-cluster')
jobs = sky.get(request_id)
for job in jobs:
    print(f"Job {job['job_id']}: {job['status']}")
```

### Batch Submission (Launch Multiple Tasks)

```python
import sky

# Launch multiple training runs with different configs
configs = [
    {'lr': 1e-3, 'batch_size': 32},
    {'lr': 1e-4, 'batch_size': 64},
    {'lr': 1e-5, 'batch_size': 128},
]

request_ids = []
for i, cfg in enumerate(configs):
    task = sky.Task(
        name=f'sweep-{i}',
        setup='pip install torch transformers',
        run=f'python train.py --lr {cfg["lr"]} --batch-size {cfg["batch_size"]}',
    )
    task.set_resources(sky.Resources(accelerators='A100:1'))

    request_id = sky.launch(
        task,
        cluster_name=f'sweep-{i}',
        idle_minutes_to_autostop=10,
        down=True,  # Auto-teardown after idle
    )
    request_ids.append(request_id)
    print(f'Launched sweep-{i}: {request_id}')

# Wait for all to complete
for rid in request_ids:
    result = sky.get(rid)
    print(f'Completed: {rid}')
```

### Wait for Completion and Get Results

```python
import sky

# Launch a task
task = sky.Task.from_yaml('train.yaml')
request_id = sky.launch(task, cluster_name='train')

# stream_and_get streams logs AND waits for completion
result = sky.stream_and_get(request_id)

# Check job status
request_id = sky.job_status('train', job_ids=[1])
statuses = sky.get(request_id)
print(f'Job 1 status: {statuses[1]}')

# Tail logs (blocking, streams to console)
exit_code = sky.tail_logs('train', job_id=1, follow=True)
print(f'Job exit code: {exit_code}')
```

### Programmatic Managed Job Launch

```python
import sky

# Create a managed job task
task = sky.Task(
    name='managed-training',
    setup='pip install torch transformers',
    run='python train.py --resume',
)
task.set_resources(sky.Resources(
    accelerators='A100:4',
    use_spot=True,
    job_recovery={
        'strategy': 'EAGER_NEXT_REGION',
        'max_restarts_on_errors': 3,
    },
))

# Launch as a managed job
request_id = sky.jobs.launch(task)
job_id = sky.get(request_id)
print(f'Managed job ID: {job_id}')

# Check managed job queue
request_id = sky.jobs.queue()
jobs = sky.get(request_id)
for job in jobs:
    print(f"Job {job['job_id']}: {job['job_name']} - {job['status']}")
```

### DAG Workflow (Task Dependencies)

```python
import sky

# Create a DAG with task dependencies
with sky.Dag() as dag:
    # Step 1: Data preprocessing
    preprocess = sky.Task(
        name='preprocess',
        setup='pip install pandas pyarrow',
        run='python preprocess.py --output /tmp/data',
    )
    preprocess.set_resources(sky.Resources(cpus='8+', memory='32+'))

    # Step 2: Training (depends on preprocessing)
    train = sky.Task(
        name='train',
        setup='pip install torch transformers',
        run='python train.py --data /tmp/data',
    )
    train.set_resources(sky.Resources(accelerators='A100:4'))

    # Define dependency: preprocess must finish before train
    preprocess >> train

# Launch the DAG (experimental)
request_id = sky.launch(dag, cluster_name='pipeline')
sky.get(request_id)
```

### Autostop Management

```python
import sky

# Set autostop: stop after 30 minutes of idleness
request_id = sky.autostop(
    cluster_name='my-cluster',
    idle_minutes=30,
)
sky.get(request_id)

# Set autodown: tear down (not just stop) after 60 minutes idle
request_id = sky.autostop(
    cluster_name='my-cluster',
    idle_minutes=60,
    down=True,
)
sky.get(request_id)

# Cancel autostop
request_id = sky.autostop(
    cluster_name='my-cluster',
    idle_minutes=-1,  # Negative value cancels autostop
)
sky.get(request_id)

# Stop and start a cluster
sky.get(sky.stop('my-cluster'))
sky.get(sky.start('my-cluster'))

# Tear down a cluster
sky.get(sky.down('my-cluster'))
```

### List GPUs Programmatically

```python
import sky

# List all available accelerators
request_id = sky.list_accelerators(
    gpus_only=True,
    name_filter='A100',
)
accelerators = sky.get(request_id)

for acc_name, offerings in accelerators.items():
    print(f'\n{acc_name}:')
    for offering in offerings:
        print(f'  {offering}')

# List accelerator counts (how many GPUs per node)
request_id = sky.list_accelerator_counts(
    gpus_only=True,
    name_filter='H100',
)
counts = sky.get(request_id)
for acc_name, available_counts in counts.items():
    print(f'{acc_name}: available in counts {available_counts}')

# Check which clouds are enabled
request_id = sky.enabled_clouds()
clouds = sky.get(request_id)
print(f'Enabled clouds: {clouds}')
```

---

## Quick Reference: SkyPilot Environment Variables

These variables are automatically set by SkyPilot on every node and are available
in `setup` and `run` commands:

| Variable | Description | Example Value |
|----------|-------------|---------------|
| `SKYPILOT_NODE_RANK` | Rank of the current node (0 = head) | `0` |
| `SKYPILOT_NODE_IPS` | Newline-separated IPs of all nodes | `10.0.0.1\n10.0.0.2` |
| `SKYPILOT_NUM_NODES` | Total number of nodes in the cluster | `2` |
| `SKYPILOT_NUM_GPUS_PER_NODE` | Number of GPUs on this node | `8` |
| `SKYPILOT_TASK_ID` | Unique task identifier | `sky-2024-01-15-abc123` |
| `SKYPILOT_CLUSTER_INFO` | JSON with cluster metadata | `{"cloud": "aws", ...}` |
| `SKYPILOT_JOB_RANK` | Job rank within a pool (pool jobs only) | `3` |
| `SKYPILOT_NUM_JOBS` | Total number of jobs in batch (pool jobs only) | `10` |

## Quick Reference: Common CLI Patterns

```bash
# Launch and auto-stop after 30 minutes idle
sky launch -c mycluster task.yaml -i 30

# Launch with auto-teardown (not just stop) after idle
sky launch -c mycluster task.yaml -i 30 --down

# Launch detached (don't stream logs)
sky launch -c mycluster task.yaml -d

# Retry until resources are available
sky launch -c mycluster task.yaml -r

# Override GPU from CLI
sky launch -c mycluster task.yaml --gpus H100:4

# Override cloud from CLI
sky launch -c mycluster task.yaml --infra aws/us-east-1

# Execute on existing cluster (skip setup)
sky exec mycluster another-task.yaml

# Execute inline command on existing cluster
sky exec mycluster -- python eval.py --checkpoint /tmp/best.pt

# Check GPU pricing
sky gpus list H100
sky gpus list A100 --all-regions
sky gpus list --infra aws
```
