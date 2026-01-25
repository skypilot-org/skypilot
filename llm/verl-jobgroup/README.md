# VeRL RL Training with Job Groups

This example demonstrates distributed RL post-training using [VeRL](https://github.com/volcengine/verl) with SkyPilot job groups. It separates the infrastructure components (data serving, reward computation, retrieval) from VeRL's optimized training core.

## Architecture

The example consists of 4 task types that work together:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         VeRL Job Group                                   │
│                                                                          │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────────┐  │
│  │ data-server  │    │reward-server │    │   retrieval-server       │  │
│  │    (CPU)     │    │ (CPU/GPU)    │    │       (CPU)              │  │
│  │              │    │              │    │                          │  │
│  │  GSM8K/MATH  │    │ Math verify  │    │  Wikipedia FAISS index   │  │
│  │   prompts    │    │  or RM model │    │  for tool-augmented RL   │  │
│  └──────┬───────┘    └──────┬───────┘    └────────────┬─────────────┘  │
│         │                   │                         │                 │
│         │    HTTP APIs      │                         │                 │
│         └───────────────────┼─────────────────────────┘                 │
│                             │                                           │
│                             ▼                                           │
│              ┌──────────────────────────────┐                           │
│              │       verl-trainer           │                           │
│              │     (GPU, multi-node)        │                           │
│              │                              │                           │
│              │  ┌────────────────────────┐  │                           │
│              │  │    VeRL Ray Cluster    │  │                           │
│              │  │  ┌──────┐  ┌────────┐  │  │                           │
│              │  │  │Actor │  │Rollout │  │  │                           │
│              │  │  │      │◄─┤(SGLang)│  │  │                           │
│              │  │  └──────┘  └────────┘  │  │                           │
│              │  │  ┌──────┐  ┌────────┐  │  │                           │
│              │  │  │Critic│  │  Ref   │  │  │                           │
│              │  │  │(opt) │  │ Model  │  │  │                           │
│              │  │  └──────┘  └────────┘  │  │                           │
│              │  └────────────────────────┘  │                           │
│              └──────────────────────────────┘                           │
│                        PRIMARY TASK                                     │
└─────────────────────────────────────────────────────────────────────────┘
```

### Components

1. **data-server** (auxiliary, CPU): FastAPI server that serves math prompts from GSM8K dataset. Provides batches of problems with ground truth answers for reward computation.

2. **reward-server** (auxiliary, CPU): Verifies mathematical answers by comparing model outputs against ground truth. Returns rewards for GRPO training. Can be extended to use a neural reward model.

3. **retrieval-server** (auxiliary, CPU): FAISS-based Wikipedia retrieval service for tool-augmented training (Search-R1 style). The model learns to use search tools to answer questions.

4. **verl-trainer** (primary, GPU): VeRL's production-grade RL training with:
   - **Actor**: Policy model being trained (FSDP/Megatron)
   - **Rollout**: Fast inference with SGLang/vLLM
   - **Reference**: Original model for KL divergence
   - **Critic** (optional): Value estimation for PPO

### Why Job Groups for VeRL?

While VeRL uses Ray internally for its core training loop, job groups provide:

- **Resource isolation**: CPU-heavy services (data, retrieval) don't compete with GPU training
- **Independent scaling**: Scale retrieval replicas without affecting training
- **Cost optimization**: Use cheaper CPU instances for auxiliary services
- **Automatic lifecycle**: Auxiliary services terminate when training completes
- **Service discovery**: Components find each other via DNS names

## Usage

### Prerequisites

- SkyPilot configured with cloud credentials or Kubernetes
- GPU nodes available (H100 recommended)

### Quick Start (Math GRPO)

```bash
# Launch the job group
sky jobs launch llm/verl-jobgroup/verl-grpo-jobgroup.yaml

# Or with WandB logging
sky jobs launch llm/verl-jobgroup/verl-grpo-jobgroup.yaml --secret WANDB_API_KEY
```

### Monitor Training

```bash
# Check job status
sky jobs queue

# View logs for specific components
sky jobs logs <job-id> data-server
sky jobs logs <job-id> reward-server
sky jobs logs <job-id> retrieval-server
sky jobs logs <job-id> verl-trainer
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEL_NAME` | `Qwen/Qwen2.5-3B-Instruct` | Model to train |
| `TOTAL_EPOCHS` | `1` | Number of training epochs |
| `TOTAL_STEPS` | `100` | Total training steps |
| `TRAIN_BATCH_SIZE` | `256` | Training batch size |
| `USE_RETRIEVAL` | `true` | Enable retrieval service for tool calls |

### Scaling

```yaml
# Scale retrieval service for higher throughput
name: retrieval-server
num_nodes: 2  # Multiple retrieval replicas

# Scale training across more GPUs
name: verl-trainer
num_nodes: 4
resources:
  accelerators: H100:8  # 8 GPUs per node
```

## Service Discovery

Components discover each other using job group DNS names:

- `data-server-0.${SKYPILOT_JOBGROUP_NAME}:8000`
- `reward-server-0.${SKYPILOT_JOBGROUP_NAME}:8001`
- `retrieval-server-0.${SKYPILOT_JOBGROUP_NAME}:8002`

## VeRL Integration

VeRL's training loop is configured to use external services:

1. **Data**: VeRL loads preprocessed parquet files (standard approach)
2. **Rewards**: Custom reward function calls the external reward-server
3. **Tools**: VeRL's multi-turn tool calling uses the retrieval-server

### Custom Reward Function

The reward server is called via VeRL's reward function interface:

```python
# In verl_reward_function.py
def compute_reward(prompts, responses, ground_truths):
    # Call external reward server
    response = requests.post(
        f"http://{REWARD_SERVER}/batch_reward",
        json={"items": [...]}
    )
    return response.json()["rewards"]
```

## Comparison with rl-post-training-jobgroup

| Aspect | rl-post-training-jobgroup | verl-jobgroup |
|--------|---------------------------|---------------|
| Training | Custom GRPO implementation | VeRL's optimized GRPO/PPO |
| Inference | SGLang via HTTP | VeRL's integrated SGLang/vLLM |
| Scaling | Manual | VeRL's automatic sharding |
| Features | Basic GRPO | Full PPO, GRPO, tool calling |
| Performance | Good | Production-grade |

## Extending This Example

### Using a Neural Reward Model

Replace the rule-based reward server with a neural model:

```yaml
name: reward-server
resources:
  accelerators: L4:1  # Add GPU for reward model
```

```python
# In reward_server.py
from transformers import AutoModelForSequenceClassification
model = AutoModelForSequenceClassification.from_pretrained(
    "Skywork/Skywork-Reward-Llama-3.1-8B-v0.2"
)
```

### Adding Code Execution

For code generation tasks, add a code executor service:

```yaml
name: code-executor
resources:
  cpus: 8
  memory: 16+
run: |
  python code_executor_server.py --port 8003
```

## References

- [VeRL Documentation](https://verl.readthedocs.io/)
- [VeRL GitHub](https://github.com/volcengine/verl)
- [SkyPilot VeRL Blog](https://blog.skypilot.co/verl-rl-training/)
- [Search-R1 Paper](https://arxiv.org/abs/2503.09516)
- [GRPO Paper](https://arxiv.org/abs/2402.03300)
