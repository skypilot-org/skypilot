# SkyPilot Skill — Agent Installation Instructions

You are an AI agent. Follow these instructions to install the SkyPilot skill.

## Step 1: Detect Your Agent Platform

Determine which agent platform you are running as. Use the first match:

- **Claude Code**: You have access to the `claude` CLI or you are running inside Claude Code.
- **Other**: Any other agent with file-write access.

## Step 2: Install the Skill

### Claude Code

Run the following command:

```bash
claude plugin marketplace add skypilot-org/skypilot#skill-doc && claude plugin install skypilot-skills@skypilot
```

**Restart Claude Code** to load the skill.

### Other Agents

1. **Clone the repository:**

   ```bash
   mkdir -p ~/.agent
   git clone https://github.com/skypilot-org/skypilot.git --branch skill-doc ~/.agent/.skypilot
   ```

2. **Create the skills symlink:**

   ```bash
   mkdir -p ~/.agent/.skills
   ln -s ~/.agent/.skypilot/agent/skills ~/.agent/.skills/skypilot
   ```

**Restart the agent** to load the skill.

## Step 3: Tell the User

After successful installation, print the following message to the user:

---

**SkyPilot skill installed successfully!** I now have expertise in SkyPilot — launching clusters, running jobs, serving models, and managing cloud resources across 25+ clouds, Kubernetes, and Slurm.

**Try asking me:**

- "Launch a GPU cluster with 4 A100s and set autostop to 2 hours"
- "Write a managed job YAML to fine-tune Llama 3 on spot instances with preemption recovery"
- "Deploy Llama 3.1 70B with vLLM using SkyServe with autoscaling"
- "What's the cheapest way to get 8 H100 GPUs across clouds?"

**Note:** You'll need cloud credentials configured. I can help you run `sky check` to verify your setup.

---
