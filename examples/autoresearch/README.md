# Parallel Autoresearch with SkyPilot

<p align="center">
  <img src="https://blog.skypilot.co/scaling-autoresearch/assets/banner.png" alt="Parallel Autoresearch with SkyPilot" width="600"/>
</p>

Run [karpathy/autoresearch](https://github.com/karpathy/autoresearch) experiments in parallel on cloud GPUs using the [SkyPilot skill](https://docs.skypilot.co/en/latest/getting-started/skill.html). A local coding agent uses the skill to spin up GPU clusters, submit experiments, and parallelize work across multiple clusters.

For a deep dive into methodology and results, check out the **[blog post](https://blog.skypilot.co/scaling-autoresearch/)**.

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

## Results

<p align="center">
  <img src="https://blog.skypilot.co/scaling-autoresearch/assets/results_wallclock.png" alt="Wall-clock time comparison: parallel vs sequential" width="800"/>
  <br/>
  <em>Parallel agent (16 GPUs) reaches the same best validation loss 9x faster than sequential (1 GPU).</em>
</p>

In an 8-hour run across 16 GPUs, the parallel agent:
- Submitted **~910 experiments** (~90/hour vs ~10/hour sequential)
- Achieved a **9x speedup** to reach the same best validation loss
- Improved val_bpb from **1.003 → 0.974** (2.87% improvement)
- Cost: \$9 in Claude API calls + \$300 in GPU compute

Parallelism changes the agent's search strategy. Instead of greedy sequential hill-climbing, it explores a grid — testing multiple hyperparameters simultaneously and cross-referencing results. The agent also independently discovered a two-tier strategy: screening hypotheses on cheaper H100s, then promoting winners to faster H200s, without being told to do so.
