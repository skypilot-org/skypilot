# Verl: State-of-the-art RL Training for LLMs


[Verl](https://github.com/volcengine/verl) is the most popular open-source reinforcement learning framework for LLMs, supporting PPO, GRPO, and other algorithms.

Also see [`search-tooling/`](https://github.com/skypilot-org/skypilot/tree/master/llm/verl/search-tooling) and this [blog](https://blog.skypilot.co/verl-tool-calling/) for tool-augmented “search” workflows (Search-R1 style), including Google Search–backed inference and a Wikipedia FAISS retrieval service used for inference and training.

## Why SkyPilot + Verl?

SkyPilot makes RL training **easy and cost-effective**:
- **Get GPUs instantly** across clouds and Kubernetes
- **3x cheaper** with managed spot instances  
- **Zero setup** - handles distributed Ray clusters automatically

## Quick Start

Launch single node agent training:

```bash
sky launch -c verl-ppo llm/verl/verl-ppo.yaml --secret WANDB_API_KEY --num-nodes 1 -y
sky launch -c verl-ppo llm/verl/verl-ppo.yaml --secret WANDB_API_KEY --secret HF_TOKEN --num-nodes 1 -y

sky launch -c verl-grpo llm/verl/verl-grpo.yaml --secret WANDB_API_KEY --num-nodes 1 -y
sky launch -c verl-grpo llm/verl/verl-grpo.yaml --secret WANDB_API_KEY --secret HF_TOKEN --num-nodes 1 -y
```

Launch a 2-node RLHF training job on the cheapest available GPUs:
```bash
sky launch -c verl llm/verl/multinode.yaml
```

Monitor training progress:
```bash
sky logs verl
```

<p align="center">
  <img src="https://imgur.com/vQoEIm6.png" alt="Verl training logs showing reward optimization" width="90%"/>
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

## Learn More

- [Verl Documentation](https://verl.readthedocs.io/)
- [Verl GitHub Repository](https://github.com/volcengine/verl)
- [SkyPilot Ray Setup Guide](https://docs.skypilot.co/en/latest/running-jobs/distributed-jobs.html#executing-a-distributed-ray-program)
