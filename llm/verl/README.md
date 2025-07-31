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

**With Weights & Biases tracking** (recommended for visualizing training curves):
```bash
sky launch -c verl llm/verl/multinode.yaml --secret WANDB_API_KEY
```

Monitor training progress:
```bash
sky logs verl
```

<p align="center">
  <img src="https://i.imgur.com/XXXXXXX.png" alt="Verl training logs showing reward optimization" width="90%"/>
</p>
<p align="center"><i>Training logs showing PPO optimization progress with reward metrics</i></p>

Access Ray dashboard:
```bash
sky status --endpoint 8280 verl
```

<p align="center">
  <img src="https://i.imgur.com/6Lwuldi.png" alt="Ray Dashboard showing distributed RLHF training" width="90%"/>
</p>
<p align="center"><i>Ray dashboard showing real-time monitoring of distributed training across multiple nodes</i></p>

## Key Features

The example trains Qwen2.5-0.5B-Instruct on the GSM8K dataset using PPO:
- **Multi-node distributed training** with automatic Ray cluster setup
- **Checkpoint persistence** to cloud storage for fault tolerance
- **Customizable models and datasets** via environment variables

## Advanced Usage

### 💰 Use Spot Instances for 3x Cost Savings

```bash
sky jobs launch -n verl-job llm/verl/multinode.yaml
```
Training automatically resumes from checkpoints if preempted.

### 🚀 Continue Experiments on the Same Cluster

```bash
# Run additional training epochs
sky exec verl llm/verl/multinode.yaml --env TOTAL_EPOCHS=10

# The YAML automatically detects and reuses the existing Ray cluster
```

### 📈 Scale to More Nodes

```bash
sky launch -c verl llm/verl/multinode.yaml --num-nodes 4
```

### 🔧 Customize Training Configuration

Modify parameters directly:
```bash
sky launch -c verl llm/verl/multinode.yaml \
  --env MODEL_NAME=meta-llama/Llama-2-7b-hf \
  --env ACTOR_LR=5e-6 \
  --env CRITIC_LR=1e-5
```

Train a larger model:
```bash
sky launch -c verl llm/verl/multinode.yaml \
  --env MODEL_NAME=Qwen/Qwen2.5-7B-Instruct \
  --gpus A100-80GB:8 --num-nodes 4
```

## Understanding the Setup

1. **Head node**: Prepares data, starts Ray head, submits training job
2. **Worker nodes**: Join Ray cluster for distributed training
3. **Smart resumption**: Ray cluster is reused if already running, avoiding restart overhead

## Troubleshooting

- **OOM errors**: Reduce batch sizes or `gpu_memory_utilization`
- **Connection issues**: Ensure ports 6385 (Ray) and 8280 (dashboard) are not blocked
- **First run is slow**: Model download happens once, subsequent runs are faster

## Learn More

- [Verl Documentation](https://verl.readthedocs.io/)
- [Verl GitHub Repository](https://github.com/volcengine/verl)
- [SkyPilot Ray Setup Guide](https://docs.skypilot.co/en/latest/running-jobs/distributed-jobs.html#executing-a-distributed-ray-program)