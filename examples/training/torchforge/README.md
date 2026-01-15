# TorchForge: Agentic RL Training with SkyPilot

[TorchForge](https://github.com/meta-pytorch/torchforge) is a PyTorch-native agentic RL library that lets you focus on algorithms—not infrastructure. It provides clear RL abstractions with a scalable implementation built on [Monarch](https://github.com/meta-pytorch/monarch), [TorchTitan](https://github.com/pytorch/torchtitan), and [vLLM](https://github.com/vllm-project/vllm).

This example demonstrates how to run TorchForge GRPO (Grouped Relative Policy Optimization) training on Kubernetes or cloud VMs using SkyPilot.

## Overview

TorchForge GRPO trains language models using reinforcement learning with the following components:
- **Trainer** (TorchTitan-based): The model being trained with FSDP/distributed training
- **Reference Model**: A frozen copy used to compute KL divergence
- **Generator** (vLLM-based): Generates responses for the RL loop
- **Reward Actor**: Evaluates generated responses

All components are orchestrated by Monarch, a distributed actor system that handles communication and fault tolerance.

## Quick Start

### Single-Node Training (Qwen3-1.7B)

Train Qwen3-1.7B on GSM8K math problems with 3+ H100 GPUs:

```bash
# Install SkyPilot
pip install "skypilot[kubernetes]"  # or skypilot[aws], skypilot[gcp], etc.

# Launch training
sky launch -c torchforge torchforge.yaml

# View logs
sky logs torchforge

# Terminate when done
sky down torchforge
```

### Larger Models (Llama 3.1 8B)

For Llama 3.1 8B, you need 5+ GPUs and a Hugging Face token:

```bash
export HF_TOKEN=<your-hugging-face-token>
sky launch -c torchforge-llama torchforge.yaml \
  --env HF_TOKEN \
  --env MODEL=meta-llama/Meta-Llama-3.1-8B-Instruct \
  --env CONFIG_FILE=apps/grpo/llama3_8b.yaml \
  --env MIN_GPUS=5
```

## Configuration

The default `torchforge.yaml` configuration:
- Uses Qwen3-1.7B model (no auth required)
- Runs on a single node with 3-8 H100 GPUs
- Trains GRPO on the GSM8K dataset
- Logs to console (W&B optional)

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEL` | `Qwen/Qwen3-1.7B` | Hugging Face model to train |
| `CONFIG_FILE` | `apps/grpo/qwen3_1_7b.yaml` | TorchForge config file |
| `MIN_GPUS` | `3` | Minimum GPUs required |
| `HF_TOKEN` | (empty) | Hugging Face token for gated models |
| `WANDB_MODE` | `disabled` | Set to `online` to enable W&B logging |

### Available Model Configurations

TorchForge includes pre-configured GRPO recipes:

| Model | Config File | Min GPUs |
|-------|-------------|----------|
| Qwen3 1.7B | `apps/grpo/qwen3_1_7b.yaml` | 3 |
| Qwen3 8B | `apps/grpo/qwen3_8b.yaml` | 5 |
| Llama 3.1 8B | `apps/grpo/llama3_8b.yaml` | 5 |

## Multi-Node Training

For larger models that require multi-node training, you can scale by increasing the number of GPUs:

```bash
# Use 8 GPUs for larger model parallelism
sky launch -c torchforge-8gpu torchforge.yaml \
  --env MIN_GPUS=8 \
  --env CONFIG_FILE=apps/grpo/qwen3_8b.yaml
```

## Understanding the Training Loop

TorchForge GRPO implements the following training loop:

1. **Sample prompts** from the dataset (GSM8K math problems)
2. **Generate responses** using vLLM with the current policy
3. **Compute rewards** based on answer correctness
4. **Compute reference log-probs** using the frozen reference model
5. **Compute advantages** using group normalization (GRPO)
6. **Update policy** using the GRPO loss (policy gradient + KL penalty)
7. **Sync weights** from trainer to generator via TorchStore
8. Repeat until convergence

## Why SkyPilot for TorchForge?

- **Simple GPU Provisioning**: SkyPilot handles GPU allocation across Kubernetes or clouds
- **Unified Interface**: Same YAML works on Kubernetes, AWS, GCP, Azure, and 20+ other clouds
- **Cost Optimization**: SkyPilot can find the cheapest available GPUs across providers
- **Fault Tolerance**: Automatic recovery from preemptions when using managed jobs

## Advanced: Multi-Node with Monarch SkyPilotJob

For advanced use cases requiring multi-node Monarch meshes, you can use the `SkyPilotJob` class to provision worker nodes programmatically. See the [Monarch SkyPilot example](https://github.com/meta-pytorch/monarch/tree/main/examples/skypilot) for details.

## Troubleshooting

### Check GPU availability
```bash
sky show-gpus --infra kubernetes
```

### View cluster logs
```bash
sky logs torchforge
```

### SSH into the cluster for debugging
```bash
ssh torchforge
```

### Common Issues

1. **CUDA out of memory**: Reduce `local_batch_size` in the config file
2. **vLLM import errors**: Ensure you're using the correct PyTorch/CUDA versions
3. **Model download fails**: Check `HF_TOKEN` for gated models

### Known Warnings During Shutdown

After training completes, you may see warnings like:

```
UserWarning: resource_tracker: '/shared_tensor_...': [Errno 2] No such file or directory
SupervisionError: Endpoint call Generator.generate() failed
```

These are **harmless shutdown race conditions** and do not indicate training failures:

- **resource_tracker warnings**: TorchStore uses shared memory for weight synchronization. During shutdown, multiple processes may try to clean up the same shared memory segments.
- **SupervisionError**: When training reaches MAX_STEPS, the training loop exits while the rollout thread may still have in-flight Generator calls. This is a known TorchForge shutdown behavior.

To verify training worked correctly, look for:
- "Reached training limit (N steps)" message
- "Pushing weights for policy version N" messages
- GRPO loss metrics (grpo_loss/total_loss, grpo_loss/kl_divergence_mean)

## Resources

- [TorchForge Documentation](https://meta-pytorch.org/torchforge)
- [TorchForge GitHub](https://github.com/meta-pytorch/torchforge)
- [Monarch Documentation](https://meta-pytorch.org/monarch)
- [TorchTitan GitHub](https://github.com/pytorch/torchtitan)
- [SkyPilot Documentation](https://docs.skypilot.co)
