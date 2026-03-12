# SkyRL: Modular Full-Stack RL Training for LLMs

[SkyRL](https://github.com/NovaSky-AI/SkyRL) is a modular, performant reinforcement learning library for LLMs, designed for real‑world agentic workloads.
Its modular design enables users to modify anything - add new environments, easily implement improvements such as asynchronous training, heterogeneous hardware support, and more!

## Why SkyPilot + SkyRL?

SkyPilot makes RL training with SkyRL easy to run and scale with best cost-efficiency:

- **Run on any AI infrastructure, including Kubernetes or clouds**
- **Zero setup** — one command takes care of provisioning, setting up and run the training.

## Quick Start

Launch a multi‑node GRPO training job on GSM8K using the cheapest available GPUs using the [YAML](https://github.com/skypilot-org/skypilot/blob/master/llm/skyrl/train.yaml):

```bash
export WANDB_API_KEY="xxx"
sky launch -c skyrl train.yaml --secret WANDB_API_KEY
```

Monitor training progress:

```bash
sky logs skyrl
```

<p align="center">
  <img src="https://i.imgur.com/2sVHs7g.png" alt="SkyPilot logs" width="90%"/>
</p>
<p align="center"><i>Logs of the training jobs</i></p>

You can also view the job status in the SkyPilot Dashboard:

```bash
sky dashboard
```

<p align="center">
  <img src="https://i.imgur.com/p35HlUy.jpeg" alt="SkyPilot Dashboard" width="90%"/>
</p>
<p align="center"><i>Dashboard showing the status of the training job</i></p>

If Weights & Biases (W&B) is configured, you can monitor the training run:

<p align="center">
  <img src="https://i.imgur.com/1a7WO7q.jpeg" alt="W&B training metrics" width="90%"/>
</p>

## Key Features

- Modular design: plug‑and‑play algorithms, environments, and hardware backends
- Scales from a single GPU to multi‑node clusters via Ray + SkyPilot
- Minimal boilerplate: add new environments quickly (often <100 LoC)

## Learn More

- [SkyRL Documentation](https://skyrl.readthedocs.io/en/latest/)
- [SkyRL GitHub Repository](https://github.com/NovaSky-AI/SkyRL)
- [SkyPilot Ray Setup Guide](https://docs.skypilot.co/en/latest/running-jobs/distributed-jobs.html#executing-a-distributed-ray-program)
