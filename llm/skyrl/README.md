# SkyRL: Modular Full-stack RL Training for LLMs

[SkyRL](https://github.com/NovaSky-AI/SkyRL) is a modular, full-stack reinforcement learning library for LLMs, designed for real-world agentic workloads. 
Its modularity enable easy implementation of real-world improvements like async training, heterogeneous hardware, and new environments with under 100 LoC.

## Why SkyPilot + SkyRL?
Skypilot makes RL training with SkyRL easy and cost effective:
- **Run anywhere** with any clouds
- **Zero setup** - automatically handles distributed Ray clusters

## Quick Start

Launch a SkyRL GSM8K GRPO multi-node training job on the cheapest available GPUs:
```bash
sky launch -c skyrl skyrl_train/examples/gsm8k/gsm8k-grpo-skypilot.yaml --secret WANDB_API_KEY="1234"
```

Monitor training progress:
```bash
sky logs skyrl
```


Access Ray dashboard:
```bash
sky status --endpoint 8280 skyrl
```

## Key Features

## Learn More

- [SkyRL Documentation](https://skyrl.readthedocs.io/en/latest/)
- [SkyRL GitHub Repository](https://github.com/NovaSky-AI/SkyRL)
- [SkyPilot Ray Setup Guide](https://docs.skypilot.co/en/latest/running-jobs/distributed-jobs.html#executing-a-distributed-ray-program)