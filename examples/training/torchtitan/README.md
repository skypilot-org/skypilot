# TorchTitan: Large-Scale LLM Training with SkyPilot

[TorchTitan](https://github.com/pytorch/torchtitan) is a PyTorch native platform for large-scale LLM training, featuring multi-dimensional parallelisms (FSDP2, Tensor/Pipeline/Context Parallel), distributed checkpointing, torch.compile, and Float8 support.

This example demonstrates how to run [TorchTitan](https://github.com/pytorch/torchtitan) on your Kubernetes clusters, or any hypersclaers, neoclouds using SkyPilot, in addition to the instructions for runnning on [Slurm](https://github.com/pytorch/torchtitan?tab=readme-ov-file#multi-node-training).

## Quick start
Here is how to finetune Llama 3.1 on 2 nodes with 8 H100 (or 8 H200):
```bash
# Install SkyPilot (if not already installed)
# More cloud setup instructions in: https://docs.skypilot.co/en/latest/getting-started/installation.html
pip install "skypilot[kubernetes,aws]"  # or your cloud: [gcp], [azure], etc.

# Launch a cluster and start training
export HF_TOKEN=... # if using a gated model from the HF Hub
sky launch -c torchtitan-multinode torchtitan.yaml --env HF_TOKEN

# Tail logs
sky logs torchtitan-multinode

# Terminate the cluster when done
sky down torchtitan-multinode
```

## Configuration

The provided `torchtitan.yaml` configuration:
- Sets up a 2-node cluster with 8 H100 (or H200) GPUs per node
- Installs PyTorch nightly and TorchTitan requirements
- Downloads the Llama 3.1 tokenizer

## Available model configurations

TorchTitan includes [pre-configured training recipes](https://github.com/pytorch/torchtitan/tree/main/torchtitan/models/llama3/train_configs) for:
- Llama 3.1 8B: `llama3_8b.toml`
- Llama 3.1 70B: `llama3_70b.toml`
- Llama 3.1 405B: `llama3_405b.toml`

Each configuration file specifies model architecture, parallelism strategies, and training hyperparameters optimized for different scales.

To use a specific training recipe, you can set it through the `CONFIG_FILE` env var:
```
sky launch -c torchtitan-multinode torchtitan.yaml \
   --env HF_TOKEN \
   --env CONFIG_FILE=./torchtitan/models/llama3/train_configs/llama3_70b.toml # relative to the torchtitan's repo
```

## Scaling Up

To scale up your training, you can increase the number of nodes or try larger models:

```bash
# Scale to more nodes
sky launch -c torchtitan-8node torchtitan.yaml --num-nodes 8

# Try different model sizes (update CONFIG_FILE in torchtitan.yaml)
sky launch -c torchtitan-llama3-70b torchtitan.yaml --env CONFIG_FILE=./torchtitan/models/llama3/train_configs/llama3_70b.toml
```

## Why SkyPilot for Distributed Training?

* **Simple multi-node setup**: SkyPilot automatically provides [environment variables](https://docs.skypilot.co/en/latest/running-jobs/environment-variables.html) (`SKYPILOT_NODE_RANK`, `SKYPILOT_NODE_IPS`, etc.) that integrate seamlessly with PyTorch distributed training - no manual networking configuration needed.
* **Auto-recovery**: Built-in fault tolerance automatically recovers from node failures and spot preemptions, resuming from checkpoints.
* **Easily run on Kubernetes or clouds without code changes**: SkyPilot offers a simple interface to run TorchTitan on any infrastructure: `sky launch --infra k8s torchtitan.yaml`
* **Launch distributed training with a single command**: SkyPilot automatically provides [environment variables](https://docs.skypilot.co/en/latest/running-jobs/environment-variables.html)(`SKYPILOT_NODE_RANK`, `SKYPILOT_NODE_IPS`, etc.) that integrate seamlessly with PyTorch distributed training - no manual networking configuration needed.
* **Auto-recovery**: Built-in fault tolerance automatically recovers from node failures and spot preemptions, resuming from checkpoints.

## Multi-node training details

The configuration automatically:
- Detects the head node IP and sets it as the master address
- Configures the correct node rank for each node
- Sets up the distributed environment for PyTorch's torchrun with key settings:
  - `--nnodes`: Uses `$SKYPILOT_NUM_NODES` to specify total number of nodes
  - `--nproc_per_node`: Uses `$SKYPILOT_NUM_GPUS_PER_NODE` for GPUs per node
  - `--node_rank`: Uses `$SKYPILOT_NODE_RANK` to identify each node's position
  - `--master_addr`: Extracts head node IP from `$SKYPILOT_NODE_IPS`
  - `--master_port`: Sets communication port to 8008 for distributed coordination
