# Verl: State-of-the-art RL Training for LLMs

[Verl](https://github.com/volcengine/verl) is the most popular open-source reinforcement learning framework for LLMs, supporting PPO, DPO, and other algorithms.

## Why SkyPilot + Verl?

SkyPilot makes RL training **easy and cost-effective**:
- **Get GPUs instantly** across clouds and Kubernetes
- **3x cheaper** with managed spot instances
- **Zero setup** - handles distributed Ray clusters automatically

## Quick Start

Launch a 2-node RLHF training job on the cheapest available GPUs:
```bash
sky launch -c verl llm/verl/multinode.yaml
```

Monitor training progress:
```bash
sky logs verl
```

## Advanced Features

**💰 Use spot instances for 3x cost savings:**
```bash
sky jobs launch -n verl-job llm/verl/multinode.yaml
```
Training automatically resumes from checkpoints if preempted.

**🚀 Continue experiments on the same cluster:**
```bash
# Run additional training epochs
sky exec verl llm/verl/multinode.yaml --env TOTAL_EPOCHS=10

# The YAML automatically detects and reuses the existing Ray cluster
```

**📈 Scale to more nodes:**
```bash
sky launch -c verl llm/verl/multinode.yaml --num-nodes 4
```

## Learn More

- [Full example with config options](https://github.com/skypilot-org/skypilot/tree/master/llm/verl)
- [Verl documentation](https://verl.readthedocs.io/)