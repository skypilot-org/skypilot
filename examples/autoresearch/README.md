# Parallel Autoresearch with SkyPilot

Run [karpathy/autoresearch](https://github.com/karpathy/autoresearch) experiments in parallel on cloud GPUs using SkyPilot. A local coding agent submits experiments to multiple GPU clusters simultaneously and tracks results via a shared S3 bucket.

`program.md` preserves the original autoresearch rules (only edit `train.py`, fixed
5-min budget, minimize `val_bpb`, simplicity criterion, never stop) and adds
SkyPilot-based parallel submission and S3 coordination.

## Architecture

```
Local Agent (Claude Code, Codex, etc.)
  |
  |-- edits train.py with idea A --> sky launch -c gpu-a experiment.yaml
  |-- edits train.py with idea B --> sky launch -c gpu-b experiment.yaml
  |-- edits train.py with idea C --> sky exec gpu-a experiment.yaml
  |
  +-- polls s3://<your-bucket>/status/* for results
```

- **Agent** runs locally, generates hypotheses, edits `train.py`, submits jobs
- **SkyPilot clusters** run the 5-min training experiments on cloud GPUs
- **S3 bucket** mounted at `/bucket` on every VM via [SkyPilot storage](https://docs.skypilot.co/en/latest/reference/storage.html) —
  experiments write status/logs as regular files, no AWS CLI needed on VMs

## Files

| File | Purpose |
|------|---------|
| `experiment.yaml` | SkyPilot task: runs one experiment, reports status to S3 |
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

# 5. Tell your agent to start
#    "Read program.md and start running parallel experiments"
```

If your agent has the [SkyPilot skill](https://docs.skypilot.co/en/latest/getting-started/skill.html)
installed, it can set up SkyPilot automatically. Otherwise ensure `sky check` passes first.
