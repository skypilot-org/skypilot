# Verl Multi-node Training Example

This example demonstrates how to run distributed training with [Verl](https://github.com/volcengine/verl) (Volcano Engine Reinforcement Learning) on SkyPilot.

## Overview

Verl is a flexible and efficient reinforcement learning framework designed for training large language models with RLHF (Reinforcement Learning from Human Feedback). This example shows how to:

- Set up a multi-node Ray cluster
- Run distributed PPO training on the GSM8K dataset
- Train the Qwen2.5-0.5B-Instruct model

## Prerequisites

- GPU nodes with sufficient memory (A100 recommended)
- Access to Hugging Face models

## Quick Start

Launch a 2-node training cluster:
```bash
sky launch -c verl-cluster verl_multinode.yaml
```

Monitor training progress:
```bash
# Stream logs
sky logs verl-cluster

# Access Ray dashboard
sky status --endpoint 8280 verl-cluster
```

Clean up resources:
```bash
sky down verl-cluster
```

## Customization

### Using Different Models

To train a different model, modify the `actor_rollout_ref.model.path` and `critic.model.path` parameters in the YAML file.

### Adjusting Cluster Size

Change `num_nodes` to scale the cluster size. Ensure you also update `trainer.nnodes` accordingly.

### Using Spot Instances

For cost savings, you can enable spot instances:
```yaml
resources:
  use_spot: true
```

### Selecting Cloud Provider

Specify a cloud provider:
```yaml
resources:
  cloud: gcp  # or aws, azure, lambda, etc.
```

## Training Parameters

The example uses conservative default parameters suitable for the small Qwen2.5-0.5B model. For larger models, you may need to adjust:

- `data.train_batch_size`: Total batch size across all nodes
- `actor_rollout_ref.rollout.gpu_memory_utilization`: GPU memory fraction for vLLM
- `trainer.total_epochs`: Number of training epochs

## Monitoring

The Ray dashboard provides real-time monitoring of:
- Job status and logs
- Resource utilization
- Worker node health

Access it via the endpoint shown by `sky status --endpoint 8280 verl-cluster`.

## Troubleshooting

1. **OOM Errors**: Reduce batch sizes or `gpu_memory_utilization`
2. **Connection Issues**: Ensure ports 6385 (Ray) and 8280 (dashboard) are not blocked
3. **Model Download**: First run may take longer due to model downloads

## References

- [Verl Documentation](https://verl.readthedocs.io/)
- [Verl GitHub Repository](https://github.com/volcengine/verl)
- [SkyPilot Documentation](https://skypilot.readthedocs.io/)