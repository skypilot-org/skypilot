# Parallel Autoresearch with SkyPilot

You are an autonomous research agent running parallel GPU experiments via SkyPilot.

## Setup

1. **Read the autoresearch rules**: Fetch and read [program.md](https://raw.githubusercontent.com/karpathy/autoresearch/refs/heads/master/program.md) from the original repo. It defines what you can/cannot modify, the goal (minimize `val_bpb`), and the simplicity criterion. Follow those rules.
2. **Load the SkyPilot skill**: Fetch and follow the [SkyPilot skill](https://raw.githubusercontent.com/skypilot-org/skypilot/refs/heads/master/agent/skills/skypilot/SKILL.md) — run its "Before You Start" bootstrap to confirm SkyPilot is installed and credentials are configured.
3. **Read the autoresearch codebase**: Read `README.md`, `prepare.py`, and `train.py` for full context.
4. **Ask about infra preference**: Ask if the user prefers a specific cloud (e.g. `--infra nebius`, `--infra kubernetes`). If so, set `infra:` in the YAML. Otherwise SkyPilot picks the cheapest option.

## Launching Experiments

Use the SkyPilot skill for all infrastructure operations. The template `experiment.yaml` defines a single experiment run. Name clusters `gpu-01`, `gpu-02`, etc. — each cluster can run multiple experiments over time.

**Launch a cluster:**
```bash
sky launch gpu-01 experiment.yaml --env EXPERIMENT_ID=exp-01 --env EXPERIMENT_DESC="baseline" -d -y
```

**Pipeline experiments on the same cluster** (back-to-back via the job queue):
```bash
sky exec gpu-01 experiment.yaml --env EXPERIMENT_ID=exp-02 --env EXPERIMENT_DESC="increase LR" -d
```

**Workdir isolation**: SkyPilot snapshots the working directory at submission time. To run different `train.py` variants in parallel, copy files to a per-experiment folder and use `--workdir`:
```bash
mkdir -p /tmp/autoresearch/exp-03
cp train.py prepare.py pyproject.toml experiment.yaml /tmp/autoresearch/exp-03/
# edit /tmp/autoresearch/exp-03/train.py
sky launch gpu-03 experiment.yaml --workdir /tmp/autoresearch/exp-03 --env EXPERIMENT_ID=exp-03 --env EXPERIMENT_DESC="wider model" -d -y
```

Keep at most **4 clusters** running at a time.

## Checking Results

Use `sky logs` to stream job output:
```bash
sky logs gpu-01      # latest job
sky logs gpu-01 2    # specific job ID
```

Or SSH in and inspect directly (workdir syncs to `~/sky_workdir`):
```bash
ssh gpu-01
cd ~/sky_workdir
tail -20 run.log
```

Check status:
```bash
sky status           # all clusters
sky queue gpu-01     # jobs on a specific cluster
```

## Tracking Results

Maintain a local `results.tsv` (tab-separated):

```
experiment_id	status	val_bpb	memory_gb	description
exp-01	keep	0.997900	44.0	baseline
exp-02	discard	1.005000	44.0	switch to GeLU
exp-03	crash	0.000000	0.0	double width (OOM)
```

Status: `keep` (improvement), `discard` (no improvement), `crash` (failed).

## The Experiment Loop

LOOP FOREVER:

1. **Check state**: Review `results.tsv`, `sky status`, `sky queue`.
2. **Pick an untried idea**.
3. **Prepare**: Copy code to a per-job folder, edit `train.py`.
4. **Submit** via `sky launch` or `sky exec` with a unique `EXPERIMENT_ID`, always detached (`-d`).
5. **Don't wait** — move on to the next idea.
6. **Periodically check** results via `sky logs` or SSH.
   - `val_bpb` improved → copy winning `train.py` back, commit.
   - Otherwise → log as `discard`.
7. **Tear down** idle clusters: `sky down gpu-01 -y`
8. **Repeat**.

**Timeout**: If a run exceeds 10 minutes, treat as failure. **Crashes**: Check logs, fix trivial issues and resubmit, or log as `crash`.

**NEVER STOP**: Do NOT pause to ask the human if you should continue. Work *indefinitely* until manually stopped. If stuck, re-read the code, combine near-misses, try radical changes.

## Cleanup

```bash
sky down -a -y
```
