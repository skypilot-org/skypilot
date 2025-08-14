# TorchTitan: Large-Scale LLM Training with SkyPilot

This example shows how to train [TorchTitan](https://github.com/pytorch/torchtitan) models using SkyPilot's multi-node capabilities.

TorchTitan is a PyTorch native platform designed for rapid experimentation and large-scale training of generative AI models, featuring:
- Multi-dimensional composable parallelisms (FSDP2, Tensor Parallel, Pipeline Parallel, Context Parallel)
- Distributed checkpointing
- torch.compile support
- Float8 support
- And many more optimizations for training LLMs at scale

## Quick Start

```bash
# Install SkyPilot (if not already installed)
pip install "skypilot[kubernetes,aws]"  # or your cloud: [gcp], [azure], etc.

# Launch a cluster and start training
export HF_TOKEN=... # if using a gated model from the HF Hub
sky launch -c torchtitan-multinode torchtitan.yaml --env HF_TOKEN

# Tail logs
sky logs torchtitan-multinode

# Stop the cluster when done
sky down torchtitan-multinode
```

## Configuration

The provided `torchtitan.yaml` configuration:
- Sets up a 2-node cluster with 8 H100 (or H200) GPUs per node
- Installs PyTorch nightly and TorchTitan requirements
- Downloads the Llama 3.1 tokenizer
- Runs distributed training using torchrun

### Customizing the Configuration

You can override various parameters without editing the YAML file:

```bash
# Use 4 nodes and train a larger model
sky launch -c torchtitan-multinode torchtitan.yaml \
   --num-nodes 4 \
   --env HF_TOKEN \
   --env CONFIG_FILE=./torchtitan/models/llama3/train_configs/llama3_70b.toml # relative to the torchtitan's repo
```

## Available Model Configurations

TorchTitan includes pre-configured training recipes for:
- Llama 3.1 8B: `llama3_8b.toml`
- Llama 3.1 70B: `llama3_70b.toml`
- Llama 3.1 405B: `llama3_405b.toml`

Each configuration file specifies model architecture, parallelism strategies, and training hyperparameters optimized for different scales.

## Multi-Node Training Details

The configuration automatically:
- Detects the head node IP and sets it as the master address
- Configures the correct node rank for each node
- Sets up the distributed environment for PyTorch's torchrun

## Cost Optimization

To reduce costs:
- Use spot instances: Add `use_spot: true` to the resources section
- Use smaller GPU types for experimentation (e.g., A100 instead of H100)
- Adjust the number of nodes based on your training requirements
