# Benchmark data

`metrics.json` holds the load-bearing numbers behind the **Scaling the inference
fleet** section of the top-level README — the raw metrics we
extracted from the run logs, so the tables there are auditable.

Six runs: **1 / 2 / 3 SGLang engines × sync / async**, Qwen3-14B, GRPO,
disaggregated (external SGLang, TP1 per engine) on a SkyPilot Job Group.
Batch 184 (`ROLLOUT_BATCH_SIZE 23 × N_SAMPLES_PER_PROMPT 8`), 2 rollout steps each.
**Step 2 is steady state** — step 1 warms sandbox pools + the prefix cache.

Keyed by `{mode}_{engines}eng`. Per run:

- `steps.{1,2}` — slime's native `perf/*` per-step timing (`step_time`,
  `rollout_time`, `train_time`, `train_wait_time`, `update_weights_time`).
- `rollouts_completed` / `rollouts_aborted` — from the example's own rollout logs.
- `sglang` — per-engine saturation from the SGLang logs: peak/median
  `#running-req` and `#queue-req`, peak KV-cache fraction, and 10-bucket
  ("decile") traces across the run. The decile traces are a readability
  downsample of the raw per-decode-batch stream (thousands of timestamped
  samples/engine); finer granularity is available from the same logs.

Single 2-step runs, so per-step variance is ~±15% — read these for the scaling
shape, not exact multipliers. Every number is parsed from logs the run itself
produces — slime's `perf/*` lines and the example's rollout logs (trainer job
log) plus SGLang's decode-batch lines (engine job logs) — so a rerun of the
README commands yields the same metrics from `sky jobs logs` (raw logs not shipped).
