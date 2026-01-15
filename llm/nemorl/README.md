# NeMo RL: Scalable Reinforcement Learning for LLMs

[NeMo RL](https://github.com/NVIDIA/NeMo-RL) is NVIDIA's scalable and efficient post-training library for reinforcement learning with large language models, supporting models from 1B to over 100B parameters with distributed training capabilities.

## Why SkyPilot + NeMo RL?

SkyPilot makes RL training with NeMo **effortless**:
- **Multi-node with zero setup** - handles distributed Ray clusters automatically
- **Run on Kubernetes** - supports both managed Kubernetes clusters and your own Kubernetes clusters
- **Out of the box InfiniBand support** - automatically enables InfiniBand support on supported clouds and Kubernetes

## Quick Start

First, set up your Hugging Face token (see [Preparation](#preparation) section for details):
```bash
export HF_TOKEN=your_hf_token_here
```

Launch a 2-node DPO training job on H100s:
```bash
sky launch -c nemorl llm/nemorl/nemorl.sky.yaml --secret HF_TOKEN
```

Monitor training progress:
```bash
sky logs nemorl
```

The example runs Direct Preference Optimization (DPO) training on **2 nodes** with 8x H100 GPUs each.

## Advanced Usage

### 📈 Scale to More Nodes

```bash
sky launch -c nemorl llm/nemorl/nemorl.sky.yaml --num-nodes 4 --secret HF_TOKEN
```

### 🔧 Customize Training Configuration

Modify DPO parameters directly in the YAML:
```bash
# Edit the run command in nemorl.sky.yaml to adjust:
# - dpo.val_global_batch_size: Validation batch size
# - checkpointing.checkpoint_dir: Output directory
# - cluster.gpus_per_node: GPU configuration
```

Train with different examples from NeMo RL repository:
```bash
# Modify the run command to use different NeMo RL examples:
# - examples/run_grpo.py: Group Relative Policy Optimization
# - examples/run_sft.py: Supervised Fine-Tuning
```

## Preparation

Before running NeMo RL training, you need to set up model access and authentication:

### 1. Request Model Access
Some models used in NeMo RL examples may require access approval. For example:
- Request access to Llama models on [Hugging Face](https://huggingface.co/meta-llama) if using Llama-based examples
- Follow the model-specific access requirements for other models

### 2. Get Your Hugging Face Token
1. Go to [Hugging Face Settings > Tokens](https://huggingface.co/settings/tokens)
2. Create a new token with "Read" permissions
3. Copy the token for use in the next step

### 3. Set Environment Variable
Add your Hugging Face token to your environment:
```bash
export HF_TOKEN="your_token_here"
```

### 4. Install SkyPilot
Install SkyPilot with your preferred cloud providers:
```bash
pip install skypilot-nightly[aws,gcp,kubernetes] 
# See: https://docs.skypilot.co/en/latest/getting-started/installation.html
```

### 5. Verify Setup
Check your infrastructure setup:
```bash
sky check
```

## Learn More

- [NeMo RL Documentation](https://docs.nvidia.com/nemo/rl/)
- [NeMo RL GitHub Repository](https://github.com/NVIDIA/NeMo-RL)
- [SkyPilot Ray Setup Guide](https://docs.skypilot.co/en/latest/running-jobs/distributed-jobs.html#executing-a-distributed-ray-program)
- [DPO Algorithm Paper](https://arxiv.org/abs/2305.18290)
