# Finetuning OpenAI gpt-oss Models with SkyPilot

![](https://i.imgur.com/TkoqCQK.png)

On August 5, 2025, OpenAI released [gpt-oss](https://openai.com/open-models/), including two state-of-the-art open-weight language models: `gpt-oss-120b` and `gpt-oss-20b`. These models deliver strong real-world performance at low cost and are available under the flexible Apache 2.0 license.

The `gpt-oss-120b` model achieves near-parity with OpenAI o4-mini on core reasoning benchmarks, while the `gpt-oss-20b` model delivers similar results to OpenAI o3-mini.

This guide walks through how to finetune both models with LoRA/full finetuning using [🤗 Accelerate](https://github.com/huggingface/accelerate).

If you're looking to run inference on `gpt-oss` models, check out [the inference example](../models/gpt-oss.md)

![Cloud Logos](https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-light.png)

## Step 0: Setup infrastructure

SkyPilot is a framework for running AI and batch workloads on any infrastructure, offering unified execution, high cost savings, and high GPU availability.

### Install SkyPilot

```bash
pip install 'skypilot[all]'
```
For more details on how to setup your cloud credentials see [SkyPilot docs](https://docs.skypilot.co).

### Choose your infrastructure

```bash
sky check
```

### Configure checkpoint storage (Optional)

Checkpoint storage is optional and only needed if you want to resume training from interruptions. By default, checkpoints are saved locally on the cluster.

To enable checkpoint persistence across cluster restarts, uncomment and configure the S3 bucket in the YAML files:

```yaml
file_mounts:
  /checkpoints:
    source: s3://my-skypilot-bucket  # change this to your bucket
```

## Step 1: Run gpt-oss models

### Full finetuning

**For `gpt-oss-20b` (smaller model):**
- Requirements: 1 node, 8x H100 GPUs
```bash
sky launch -c gpt-oss-20b-sft gpt-oss-20b-sft.yaml
```

**For `gpt-oss-120b` (larger model):**
- Requirements: 4 nodes, 8x H200 GPUs each
```bash
sky launch -c gpt-oss-120b-sft gpt-oss-120b-sft.yaml
```

```yaml
# gpt-oss-120b-sft.yaml
resources:
  accelerators: H200:8
  network_tier: best

file_mounts:
  /sft: ./sft
  /checkpoints:
    source: s3://my-skypilot-bucket  # change this to your bucket

envs:
  WANDB_PROJECT: gpt-oss-120b-sft
  WANDB_RESUME: allow
  WANDB_API_KEY: ""  # optionally, enable WandB tracking by providing the API key

num_nodes: 4

setup: |
  conda install cuda -c nvidia
  uv venv ~/training --seed --python 3.10
  source ~/training/bin/activate
  uv pip install torch --index-url https://download.pytorch.org/whl/cu128
  uv pip install "trl>=0.20.0" "peft>=0.17.0" "transformers>=4.55.0"
  uv pip install deepspeed
  uv pip install git+https://github.com/huggingface/accelerate.git@c0a3aefea8aa5008a0fbf55b049bd3f0efa9cbf2
  uv pip install wandb

  uv pip install nvitop

run: |
  export WANDB_RUN_ID=$SKYPILOT_TASK_ID
  export WANDB_NAME=run-$SKYPILOT_TASK_ID
  source ~/training/bin/activate

  MASTER_ADDR=$(echo "$SKYPILOT_NODE_IPS" | head -n1)
  NP=$(($SKYPILOT_NUM_GPUS_PER_NODE * $SKYPILOT_NUM_NODES))

  accelerate launch \
    --config_file /sft/fsdp2_120b.yaml \
    --num_machines $SKYPILOT_NUM_NODES \
    --num_processes $NP \
    --machine_rank $SKYPILOT_NODE_RANK \
    --main_process_ip $MASTER_ADDR \
    --main_process_port 29500 \
    /sft/train.py --model_id openai/gpt-oss-120b --resume_from_checkpoint
```

### LoRA finetuning

**For `gpt-oss-20b` with LoRA:**
- Requirements: 1 node, 2x H100 GPU
```bash
sky launch -c gpt-oss-20b-lora gpt-oss-20b-lora.yaml
```

**For `gpt-oss-120b` with LoRA:**
- Requirements: 1 node, 8x H100 GPUs
```bash
sky launch -c gpt-oss-120b-lora gpt-oss-120b-lora.yaml
```

## Step 2: Monitor and get results

Once your finetuning job is running, you can monitor the progress and retrieve results:

```bash
# Check job status
sky status

# View logs
sky logs <cluster-name>

# Download results when complete
sky down <cluster-name>
```

### Optional: WandB tracking

To enable experiment tracking with Weights & Biases, set your API key in the YAML configuration:

```yaml
envs:
  WANDB_API_KEY: "your-wandb-api-key"
```

Each training run will automatically use a unique run ID based on the SkyPilot task ID for easy tracking and resuming.

### Example full finetuning progress

Here's what you can expect to see during training - the loss should decrease and token accuracy should improve over time:

#### gpt-oss-20b training progress

```
Training Progress for gpt-oss-20b on Nebius:
  6%|▋         | 1/16 [01:18<19:31, 78.12s/it]
{'loss': 2.2344, 'grad_norm': 17.139, 'learning_rate': 0.0, 'num_tokens': 51486.0, 'mean_token_accuracy': 0.5436, 'epoch': 0.06}

 12%|█▎        | 2/16 [01:23<08:10, 35.06s/it]
{'loss': 2.1689, 'grad_norm': 16.724, 'learning_rate': 0.0002, 'num_tokens': 105023.0, 'mean_token_accuracy': 0.5596, 'epoch': 0.12}

 25%|██▌       | 4/16 [01:34<03:03, 15.26s/it]
{'loss': 2.1548, 'grad_norm': 3.983, 'learning_rate': 0.000192, 'num_tokens': 214557.0, 'mean_token_accuracy': 0.5182, 'epoch': 0.25}

 50%|█████     | 8/16 [01:56<00:59,  7.43s/it]
{'loss': 2.1323, 'grad_norm': 3.460, 'learning_rate': 0.000138, 'num_tokens': 428975.0, 'mean_token_accuracy': 0.5432, 'epoch': 0.5}

 75%|███████▌  | 12/16 [02:15<00:21,  5.50s/it]
{'loss': 1.4624, 'grad_norm': 0.888, 'learning_rate': 6.5e-05, 'num_tokens': 641021.0, 'mean_token_accuracy': 0.6522, 'epoch': 0.75}

100%|██████████| 16/16 [02:34<00:00,  4.88s/it]
{'loss': 1.1294, 'grad_norm': 0.713, 'learning_rate': 2.2e-05, 'num_tokens': 852192.0, 'mean_token_accuracy': 0.7088, 'epoch': 1.0}

Final Training Summary:
{'train_runtime': 298.36s, 'train_samples_per_second': 3.352, 'train_steps_per_second': 0.054, 'train_loss': 2.086, 'epoch': 1.0}
✓ Job finished (status: SUCCEEDED).
```

Memory and GPU utilization using [nvitop](https://github.com/XuehaiPan/nvitop)

![nvitop](https://i.imgur.com/pGqj9RD.png)

#### gpt-oss-120b training progress

```
Training Progress for gpt-oss-120b on 4 nodes:
  3%|▏         | 1/32 [03:45<116:23, 225.28s/it]
  6%|▋         | 2/32 [06:12<90:21, 181.05s/it]
  9%|▉         | 3/32 [08:45<71:22, 147.67s/it]
 12%|█▎        | 4/32 [11:18<59:44, 128.01s/it]
 25%|██▌       | 8/32 [22:36<67:48, 169.50s/it]
 44%|████▍     | 14/32 [29:03<43:37, 145.41s/it]
```

Memory and GPU utilization using [nvitop](https://github.com/XuehaiPan/nvitop)

![nvitop](https://i.imgur.com/dAov9ud.png)

## Configuration files

You can find the complete configurations in [the following directory](https://github.com/skypilot-org/skypilot/blob/master/llm/gpt-oss-finetuning/).
