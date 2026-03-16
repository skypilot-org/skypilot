# Parallel Autoresearch with SkyPilot

Run [karpathy/autoresearch](https://github.com/karpathy/autoresearch) experiments in parallel on cloud GPUs using the [SkyPilot skill](https://docs.skypilot.co/en/latest/getting-started/skill.html). A local coding agent uses the skill to spin up VMs, submit experiments, and parallelize work across multiple GPU clusters as it sees fit.

`program.md` preserves the original autoresearch rules (only edit `train.py`, fixed
5-min budget, minimize `val_bpb`, simplicity criterion, never stop) and delegates
all infrastructure operations to the SkyPilot skill.

## Architecture

```
Local Agent (Claude Code, Codex, etc.)
  |
  |-- uses the SkyPilot skill to:
  |     - launch GPU clusters
  |     - submit experiment jobs (from experiment.yaml)
  |     - check job status and stream logs
  |     - tear down idle clusters
  |
  |-- edits train.py with hypotheses
  |-- polls s3://<your-bucket>/status/* for results
```

- **Agent** runs locally, generates hypotheses, edits `train.py`
- **SkyPilot skill** handles all infrastructure — the agent tells it what to do (launch a cluster, submit a job, check logs) and the skill translates that into the right SkyPilot commands
- **S3 bucket** mounted at `/bucket` on every VM via [SkyPilot storage](https://docs.skypilot.co/en/latest/reference/storage.html) — experiments write status/logs as regular files

## Files

| File | Purpose |
|------|---------|
| `experiment.yaml` | SkyPilot task template: runs one experiment, reports status to S3 |
| `program.md` | Agent instructions (give this to your coding agent) |

## Quick Start

```bash
# 1. Clone autoresearch
git clone https://github.com/karpathy/autoresearch.git
cd autoresearch

# 2. Copy SkyPilot files into the repo
cp /path/to/this/example/experiment.yaml .
cp /path/to/this/example/program.md .

# 3. Set your S3 bucket name in experiment.yaml (replace the placeholder in file_mounts.source)

# 4. Prepare data
pip install uv && uv sync && uv run prepare.py

# 5. Install the SkyPilot skill for your agent
#    See: https://docs.skypilot.co/en/latest/getting-started/skill.html

# 6. Tell your agent to start
#    "Read program.md and start running parallel experiments"
```

The SkyPilot skill handles installation, credential setup, and all cluster
operations. If SkyPilot isn't installed yet, the skill will guide through that
too.
