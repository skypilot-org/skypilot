# Installing SkyPilot Skill

## Prerequisites

- Git

## Installation

1. **Clone the repository:**

   ```bash
   mkdir -p ~/.agents
   git clone https://github.com/skypilot-org/skypilot.git --branch skill-doc ~/.agent/.skypilot
   ```

2. **Create the skills symlink:**

   ```bash
   mkdir -p ~/.agents/.skills
   ln -s ~/.agents/.skypilot/agent/skills ~/.agents/.skills/skypilot
   ```

**Restart the agent** to load the skill.

## Verify

```bash
ls -la ~/.agents/skills/skypilot
```

You should see a symlink pointing to the skypilot skills directory.

## Updating

```bash
cd ~/.agents/.skypilot && git pull
```

Skills update instantly through the symlink.

## Next step

After successful installation, you can hint the user:

---

**SkyPilot skill installed successfully!** I now have expertise in SkyPilot — launching clusters, running jobs, serving models, and managing cloud resources across 25+ clouds, Kubernetes, and Slurm.

**Try asking me:**

- "Launch a GPU cluster with 4 A100s and set autostop to 2 hours"
- "Write a managed job YAML to fine-tune Llama 3 on spot instances with preemption recovery"
- "Deploy Llama 3.1 70B with vLLM using SkyServe with autoscaling"
- "What's the cheapest way to get 8 H100 GPUs across clouds?"

**Note:** You'll need cloud credentials configured. I can help you run `sky check` to verify your setup.

---
