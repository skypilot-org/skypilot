# autoresearch

This is an experiment to have the LLM do its own research, running parallel
experiments on cloud GPUs via SkyPilot.

## Setup

To set up a new experiment, work with the user to:

1. **Agree on a run tag**: propose a tag based on today's date (e.g. `mar5`).
2. **Create the main branch**: `git checkout -b autoresearch/<tag>` from current master. This is the "best known" branch — only winning experiments get merged here.
3. **Read the in-scope files**: The repo is small. Read these files for full context:
   - `README.md` — repository context.
   - `prepare.py` — fixed constants, data prep, tokenizer, dataloader, evaluation. Do not modify.
   - `train.py` — the file you modify. Model architecture, optimizer, training loop.
4. **Ask for S3 bucket name**: Ask the user which S3 bucket to use for sharing results between experiments (e.g. `s3://my-autoresearch-bucket`). Use this bucket everywhere below instead of the placeholder. Update `experiment.yaml` to reference the chosen bucket under `file_mounts`.
5. **Verify data exists**: Check that `~/.cache/autoresearch/` contains data shards and a tokenizer. If not, tell the human to run `uv run prepare.py`.
6. **Verify SkyPilot**: Run `sky check` to confirm cloud credentials are configured. If SkyPilot is not installed, install it first (`pip install skypilot-nightly[aws]`). If you have the SkyPilot agent skill available, use it for guidance.
7. **Ask about infra preference**: Ask the user if they have a preference for a specific cloud or infra (e.g. `--infra nebius`, `--infra aws`, `--infra kubernetes`). If they do, pass `--infra <choice>` to every `sky launch` command. If not, omit it — SkyPilot will automatically pick the cheapest available option.
8. **Initialize shared results**: Create the results header in S3:
   ```
   echo -e "experiment_id\tstatus\tval_bpb\tmemory_gb\tdescription" > /tmp/results.tsv
   aws s3 cp /tmp/results.tsv s3://<YOUR-BUCKET>/results.tsv
   ```
9. **Confirm and go**: Confirm setup looks good.

Once you get confirmation, kick off the experimentation.

## Experimentation

Unlike the original autoresearch where each experiment runs locally on a single GPU,
here experiments run on cloud GPUs via SkyPilot. You submit experiments using
`experiment.yaml`, which launches a VM with a GPU, mounts the S3 bucket at `/bucket`,
runs training, and writes results to the bucket.

**What you CAN do:**
- Modify `train.py` — this is the only file you edit. Everything is fair game: model architecture, optimizer, hyperparameters, training loop, batch size, model size, etc.

**What you CANNOT do:**
- Modify `prepare.py`. It is read-only. It contains the fixed evaluation, data loading, tokenizer, and training constants (time budget, sequence length, etc).
- Install new packages or add dependencies. You can only use what's already in `pyproject.toml`.
- Modify the evaluation harness. The `evaluate_bpb` function in `prepare.py` is the ground truth metric.

**The goal is simple: get the lowest val_bpb.** Since the time budget is fixed, you don't need to worry about training time — it's always 5 minutes. Everything is fair game: change the architecture, the optimizer, the hyperparameters, the batch size, the model size. The only constraint is that the code runs without crashing and finishes within the time budget.

**VRAM** is a soft constraint. Some increase is acceptable for meaningful val_bpb gains, but it should not blow up dramatically. When increasing model size (depth, width), reduce `DEVICE_BATCH_SIZE` first — with `ASPECT_RATIO=64`, depth 10+ can easily OOM at batch_size=128 on a single H100.

**Simplicity criterion**: All else being equal, simpler is better. A small improvement that adds ugly complexity is not worth it. Conversely, removing something and getting equal or better results is a great outcome — that's a simplification win. When evaluating whether to keep a change, weigh the complexity cost against the improvement magnitude. A 0.001 val_bpb improvement that adds 20 lines of hacky code? Probably not worth it. A 0.001 val_bpb improvement from deleting code? Definitely keep. An improvement of ~0 but much simpler code? Keep.

**The first run**: Your very first run should always be to establish the baseline, so you will submit the training script as is.

## Output format

Once the script finishes it prints a summary like this:

```
---
val_bpb:          0.997900
training_seconds: 300.1
total_seconds:    325.9
peak_vram_mb:     45060.2
mfu_percent:      39.80
total_tokens_M:   499.6
num_steps:        953
num_params_M:     50.3
depth:            8
```

The key metric is `val_bpb`. Experiment jobs write this to `status/<id>.txt` in the
shared S3 bucket automatically.

## Submitting experiments with SkyPilot

Each experiment runs on a cloud GPU via SkyPilot. The S3 bucket `s3://<YOUR-BUCKET>`
is mounted at `/bucket` on every VM, so experiment jobs write status and logs as
regular files.

**Cluster naming**: Clusters are long-lived compute resources — name them by purpose or
track, not by experiment number. Use names like `gpu-a`, `gpu-b`, `arch-track`,
`opt-track`, etc. Experiment IDs (sequential like `exp-01`, `exp-02`) identify
individual runs and are passed via `--env EXPERIMENT_ID`.

**First experiment on a new cluster** — use `sky launch -d` (detached):
```
# Edit train.py with your experimental change, then:
sky launch experiment.yaml -c gpu-a -d \
  --env EXPERIMENT_ID=exp-01 \
  --env EXPERIMENT_DESC="baseline run"
```

**Subsequent experiments on the same cluster** — use `sky exec -d`:
```
# Edit train.py with a new idea, then:
sky exec gpu-a experiment.yaml -d \
  --env EXPERIMENT_ID=exp-02 \
  --env EXPERIMENT_DESC="increase LR to 0.04"
```

Jobs queue on the cluster and run sequentially (each experiment uses the full GPU).
Submit the next experiment right away — it starts as soon as the current one finishes,
eliminating idle time between runs.

**Pipeline experiments** — queue 2-3 experiments per cluster to keep GPUs saturated:
```
# Queue two experiments back-to-back on the same cluster:
sky exec gpu-a experiment.yaml -d \
  --env EXPERIMENT_ID=exp-05 --env EXPERIMENT_DESC="idea A"
sky exec gpu-a experiment.yaml -d \
  --env EXPERIMENT_ID=exp-06 --env EXPERIMENT_DESC="idea B"
# exp-06 starts automatically when exp-05 finishes — no idle gap.
```

**Parallel experiments on separate clusters:**
```
sky launch experiment.yaml -c arch-track -d \
  --env EXPERIMENT_ID=exp-03 \
  --env EXPERIMENT_DESC="double model width"

sky launch experiment.yaml -c opt-track -d \
  --env EXPERIMENT_ID=exp-04 \
  --env EXPERIMENT_DESC="muon LR 0.05"
```

**Avoiding workdir conflicts**: Since `sky launch` and `sky exec` sync the working
directory at submission time, parallel experiments need separate folders to avoid one
submission picking up another's code. Create a folder **per queued job** and copy the
code before submitting:
```
# For each parallel experiment, create a separate workdir:
mkdir -p /tmp/autoresearch/gpu-a
cp train.py prepare.py pyproject.toml experiment.yaml /tmp/autoresearch/gpu-a/
# Edit /tmp/autoresearch/gpu-a/train.py with idea A, then:
sky launch experiment.yaml -c gpu-a -d --workdir /tmp/autoresearch/gpu-a \
  --env EXPERIMENT_ID=exp-03 --env EXPERIMENT_DESC="idea A"

# Meanwhile, a different folder for a different cluster:
mkdir -p /tmp/autoresearch/gpu-b
cp train.py prepare.py pyproject.toml experiment.yaml /tmp/autoresearch/gpu-b/
# Edit /tmp/autoresearch/gpu-b/train.py with idea B, then:
sky launch experiment.yaml -c gpu-b -d --workdir /tmp/autoresearch/gpu-b \
  --env EXPERIMENT_ID=exp-04 --env EXPERIMENT_DESC="idea B"
```
**Important**: When pipelining multiple jobs on the same cluster, each queued job
needs its own workdir folder too, since the workdir is snapshotted at submission time.
For example, to queue two jobs on `gpu-a`, use `/tmp/autoresearch/gpu-a-exp05/` and
`/tmp/autoresearch/gpu-a-exp06/`.

**Cluster limit**: Keep at most **4 clusters** running at a time (default). Before
launching a new cluster, check `sky status` — if 4 are already up, either reuse one
with `sky exec` or wait for one to finish and tear it down first. You can submit
multiple sequential experiments to the same cluster using `sky exec`.

## Checking status

Poll experiment status from S3 (locally):
```
# Check a specific experiment
aws s3 cp s3://<YOUR-BUCKET>/status/exp-01.txt -

# List all experiment statuses
for f in $(aws s3 ls s3://<YOUR-BUCKET>/status/ | awk '{print $4}'); do
  echo "=== $f ===" && aws s3 cp s3://<YOUR-BUCKET>/status/$f -
done
```

Check SkyPilot cluster and job queue status:
```
sky status          # cluster overview
sky queue gpu-a     # see all queued/running/finished jobs on a cluster
sky logs gpu-a 3    # stream logs for job ID 3 (from sky queue output)
```

Since all jobs are submitted detached (`-d`), `sky queue` is your primary way to see
what's running, queued, and completed on each cluster. Use S3 polling for experiment
results (val_bpb, crash status).

## Logging results

When an experiment finishes, log it to the shared `results.tsv` (tab-separated, NOT comma-separated — commas break in descriptions).

The TSV has a header row and 5 columns:

```
experiment_id	status	val_bpb	memory_gb	description
```

1. experiment ID (matches EXPERIMENT_ID used at submission)
2. val_bpb achieved (e.g. 1.234567) — use 0.000000 for crashes
3. peak memory in GB, round to .1f (e.g. 12.3 — divide peak_vram_mb by 1024) — use 0.0 for crashes
4. status: `keep`, `discard`, or `crash`
5. short text description of what this experiment tried

Example:

```
experiment_id	status	val_bpb	memory_gb	description
exp-01	keep	0.997900	44.0	baseline
exp-02	keep	0.993200	44.2	increase LR to 0.04
exp-03	discard	1.005000	44.0	switch to GeLU activation
exp-04	crash	0.000000	0.0	double model width (OOM)
```

To update:
```
aws s3 cp s3://<YOUR-BUCKET>/results.tsv /tmp/results.tsv
echo -e "exp-01\tkeep\t0.9979\t44.0\tbaseline" >> /tmp/results.tsv
aws s3 cp /tmp/results.tsv s3://<YOUR-BUCKET>/results.tsv
```

## The experiment loop

**Git strategy**: Work on a single branch (`autoresearch/<tag>`). Since parallel
experiments are isolated in separate folders (see "Avoiding workdir conflicts"
above), you don't need per-experiment branches. Commit winning changes directly
to the main branch.

LOOP FOREVER:

1. **Check shared state**: Download `results.tsv` and poll `status/` in the bucket to see what's running and what's been tried.
2. **Pick an idea** that hasn't been tried and isn't currently running.
3. **Prepare the experiment**: Copy code to a per-cluster folder (see "Avoiding workdir conflicts"), edit `train.py` there with the experimental change.
4. **Submit** the experiment with `-d` (detached): Use `sky launch -d` (new cluster) or `sky exec -d` (existing cluster) with a unique EXPERIMENT_ID. Pipeline 2-3 jobs per cluster when you have ideas ready — they queue and run back-to-back with no idle gap.
5. **Don't wait** — detached mode returns immediately. Move on to the next idea or check results.
6. **Periodically check** S3 for completed experiments. Record results in `results.tsv`.
   - If val_bpb improved (lower than current best): copy the winning `train.py` back to the repo, commit it to `autoresearch/<tag>`. This becomes the new baseline for future ideas.
   - If val_bpb is equal or worse: log as `discard` and move on.
7. **Tear down** idle clusters with `sky down <cluster>` to save costs.
8. **Repeat**. Use results from completed experiments to inform next ideas. If you feel stuck, re-read the code, try combining previous near-misses, or try more radical architectural changes.

**Timeout**: Each experiment takes ~5 minutes (+ startup overhead). If a run exceeds 10 minutes, treat it as a failure.

**Crashes**: If a run crashes (OOM, bug, etc.), check `logs/<id>_error.txt` in the bucket. If it's a trivial fix (typo, missing import), fix and resubmit. If the idea is fundamentally broken, log "crash" and move on.

**NEVER STOP**: Once the experiment loop has begun (after the initial setup), do NOT pause to ask the human if you should continue. Do NOT ask "should I keep going?" or "is this a good stopping point?". The human might be asleep, or gone from a computer and expects you to continue working *indefinitely* until you are manually stopped. You are autonomous. If you run out of ideas, think harder — read papers referenced in the code, re-read the in-scope files for new angles, try combining previous near-misses, try more radical architectural changes. The loop runs until the human interrupts you, period.

## Cleanup

```
sky down -a  # tear down all clusters
```
