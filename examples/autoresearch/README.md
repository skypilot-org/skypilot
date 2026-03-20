# Parallel Autoresearch with SkyPilot

Run [karpathy/autoresearch](https://github.com/karpathy/autoresearch) experiments in parallel on cloud GPUs using the [SkyPilot skill](https://docs.skypilot.co/en/latest/getting-started/skill.html). A local coding agent uses the skill to spin up GPU clusters, submit experiments, and parallelize work across multiple clusters.

## SkyPilot Dashboard

![SkyPilot Dashboard with 4 GPU clusters](https://i.imgur.com/g05SFUR.png)

## Architecture

```
Local Agent (Claude Code, Codex, etc.)
  |
  |-- uses the SkyPilot skill to:
  |     - launch GPU clusters
  |     - submit experiment jobs (from experiment.yaml)
  |     - check job status and stream logs
  |     - SSH into clusters to inspect results
  |     - tear down idle clusters
  |
  |-- edits train.py with hypotheses
  |-- tracks results in a local results.tsv
```

- **Agent** runs locally, generates hypotheses, edits `train.py`
- **SkyPilot skill** handles all infrastructure — the agent tells it what to do and the skill translates that into SkyPilot commands
- **SSH access** — the agent SSHes into clusters to check experiment output directly

## Files

| File | Purpose |
|------|---------|
| `experiment.yaml` | SkyPilot task template: runs one experiment on a GPU cluster |
| `instructions.md` | Agent instructions for using SkyPilot (give this to your coding agent) |

The original `program.md` from the [autoresearch repo](https://github.com/karpathy/autoresearch) is fetched automatically during setup. It contains the experiment rules (what to modify, goals, constraints).

## Prerequisites

1. **SkyPilot** — Install following the [SkyPilot installation guide](https://docs.skypilot.co/en/latest/getting-started/installation.html).
2. **Cloud credentials** — Configure at least one cloud provider by running `sky check`. See the [cloud setup docs](https://docs.skypilot.co/en/latest/getting-started/installation.html#appendix-cloud-access-for-local-skypilot) for details.

## Quick Start

```bash
# 1. Clone autoresearch
git clone https://github.com/karpathy/autoresearch.git
cd autoresearch

# 2. Copy SkyPilot files into the repo
cp /path/to/this/example/experiment.yaml .
cp /path/to/this/example/instructions.md .

# 3. Prepare data
pip install uv && uv sync && uv run prepare.py

# 4. Install the SkyPilot skill for your agent
#    See: https://docs.skypilot.co/en/latest/getting-started/skill.html

# 5. Tell your agent to start
#    "Read instructions.md and start running parallel experiments"
```

The SkyPilot skill handles installation, credential setup, and all cluster operations. If SkyPilot isn't installed yet, the skill will guide through that too.
