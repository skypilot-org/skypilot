# Scale Agentic RL on your own Infrastructure — slime on SkyPilot Job Groups

An end-to-end RL example: train a coding agent (**Qwen3-14B** by default) with
**[slime](https://github.com/THUDM/slime)** on **SkyPilot Job Groups**, scaling the
components of the RL stack independently.

A Job Group gives us the pieces to scale slime to larger workloads:

- **Gang-scheduled jobs** — the **trainer cluster** (Megatron, RolloutManager) and **N inference clusters**
(SGLang engines) run as independent jobs that start, run, and stop together.
- **Discovery** — jobs reach each other by stable hostname (`sglang-0.<group>`), so the trainer cluster's router sends rollout traffic and synchronization calls to each engine.
- **Heterogeneous placement** — this example uses H100s throughout, but jobs can sit
on different GPUs (e.g. cheaper hardware for inference) via `resources.accelerators`.

> [!NOTE]
> This example also uses **[SkyPilot Sandboxes](https://docs.skypilot.ai/en/latest/sandboxes.html)**, to run the agent's untrusted code and grade it for reward.


## Architecture

![Architecture](https://raw.githubusercontent.com/skypilot-org/skypilot/master/llm/slime/images/architecture.svg)

The **trainer cluster** runs the `RolloutManager` (agent rollouts + router) on CPU and
the **trainer** (Megatron, GRPO) on GPU; each **inference cluster** serves one SGLang
engine that the rollouts generate against.

A single agent loop looks like:

1. **Claim** a warm sandbox and check out the buggy commit.
2. **Act** — the agent (mini-swe-agent) loops: each turn generates through slime's
  `OpenAIAdapter` → router → an engine (tokens + logprobs captured), then runs its
   tool call via `sandbox.exec`.
3. **Grade** — reapply the agent's diff over the hidden tests, run the repo's test
  command, parse with SWE-smith; resolved ⇒ reward 1.
4. **Emit** — `finish_session` drains the trajectory into loss-masked training samples.



## Requirements

- SkyPilot with **Job Groups** and **Sandboxes** enabled. Jobs authenticate back to the API server automatically, so no token setup is needed for Sandbox claims.
- **[SkyPilot Sandboxes](https://docs.skypilot.ai/en/latest/sandboxes.html)** — fast, isolated rollout/eval pods on your own Kubernetes. Connecting to a Sandboxes-enabled API server ships the `sky.sandbox` SDK wheel to `~/.sky/bin/wheels/`, which the trainer mounts and installs. Check with `ls ~/.sky/bin/wheels/` — it should list `skypilot_sandbox_sdk-*.whl`.
- **A Kubernetes cluster with 5–7 free H100s** for the 1, 2, and 3 engine runs. SkyPilot places the jobs for you, whether that capacity is on one 8×H100 node or fragmented across several.
- A ReadWriteMany (RWX) storage class.



## Running it

```bash
# One-time: the RWX volume the trainer + engine(s) share for disk weight-sync.
sky volumes apply policy-volume.yaml

# Optional: wandb logging. --secret picks the value up from your environment
# (and keeps it redacted in logs / dashboard / the stored request).
export WANDB_API_KEY=<key>

# Launch the disaggregated coding-agent RL Job Group.
sky jobs launch -n coding-agent coding-agent.yaml --secret WANDB_API_KEY
```

Scale the inference fleet to two or three engines — same Job Group, more SGLang jobs:

```bash
sky jobs launch -n coding-agent-2eng coding-agent-2engine.yaml --secret WANDB_API_KEY

sky jobs launch -n coding-agent-3eng coding-agent-3engine.yaml --secret WANDB_API_KEY
```

Each additional engine is an identical SGLang job in the YAML, plus its name in the trainer's `SGLANG_MEMBERS` list.

## Layout

The rollout logic is `code/generate.py` (rollout fn),
`code/sandbox_env.py` (sandbox pool helper), and `code/dataset_swesmith.py` (dataset
loader).

Each job's setup/run lives in `scripts/` — `engine-{setup,run}.sh`
(SGLang server) and `trainer-{setup,run}.sh` (deps + weight-sync volume, engine discovery, sandbox pools).

`trainer-run.sh` calls `run-slime.sh`, which builds the slime command from env vars.

## Configuring

Every axis is an env var in the trainer's YAML:


| Env                                           | Meaning                                                                                                                | Default    |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- | ---------- |
| `SLIME_MODE`                                  | `async` (overlap rollout + train) or `sync` (serialize them)                                                           | async      |
| `WEIGHT_TRANSPORT`                            | `disk` (shared RWX volume) or `nccl`                                                                                   | disk       |
| `WEIGHT_MODE`                                 | `delta` (changed bytes only) or `full`                                                                                 | delta      |
| `ROLLOUT_BATCH_SIZE` × `N_SAMPLES_PER_PROMPT` | concurrent rollouts (= sandbox claims); global batch is derived from these                                             | 23 × 8     |
| `SANDBOX_POOL_REPLICAS`                       | warm sandboxes **per repo pool** (total ≈ repos × this); ≥ concurrent rollouts for all-warm claims                     | 184        |
| `AGENT_MAX_TURNS`                             | agent turns per instance                                                                                               | 20         |
| `SWESMITH_REPOS`                              | repos to train on (one warm pool per repo image); empty `""` = **all** repos                                           | markupsafe |
| `ROLLOUT_SHUFFLE`                             | shuffle instances each step (keep on for real training); `0` = deterministic — we used `0` for reproducible benchmarks | 1          |
| `SANDBOX_NO_POOL`                             | `1` = skip warm pools, create on-demand ad-hoc sandbox per claim                                                       | 0          |


**Warm pools vs. on-demand sandboxes.** With pools on, the run pre-warms `SANDBOX_POOL_REPLICAS`
boxes per repo image (total ≈ repos × replicas) so claims hit a warm box. `SANDBOX_NO_POOL=1` skips
pre-warming and creates an on-demand sandbox per claim (a cold image pull) instead. Either way, a
claim that can't get a warm box falls back to on-demand, so a partial or slow warm never stalls the run.

## Scaling the inference fleet on our own H100s

We ran this example on our own H100s with a single inference cluster. slime's built-in metrics make the bottleneck obvious: each **step** is mostly **rollout** (train ~400 s, rollout >1200 s), and the rollout is itself **inference-bound**. Generation requests pile up in a growing queue:

```
1 engine · #queue-req over time over 2 steps (peak 122):  ▁▆███▇▆▅▄▃▂▁▁▂▇██▇▆▅▄▂▁▁
```

We used Job Groups to scale inference engines, and tracked the effect on end-to-end latency and queued requests. The following tables show results across engine counts and async vs sync mode.

**Results**

*sync mode* — rollout then train, so **step ≈ rollout + train**:


| engines | rollout (s/step) | train (s/step) | step (s) | #queue-req (peak / median) |
| ------- | ---------------- | -------------- | -------- | -------------------------- |
| 1       | 1268             | ~400           | 1697     | 122 / 62                   |
| 2       | 643              | ~400           | 1047     | 39 / 0                     |
| 3       | 620              | ~400           | 1032     | 4 / 0                      |


*async mode* — train overlaps the rollout, so **step ≈ rollout**:


| engines | rollout (s/step) | train (s/step) | step (s) | #queue-req (peak / median) |
| ------- | ---------------- | -------------- | -------- | -------------------------- |
| 1       | 1215             | ~400           | 1200     | 125 / 68                   |
| 2       | 805              | ~400           | 840      | 40 / 0                     |
| 3       | 647              | ~400           | **661**  | 9 / 0                      |


Step time falls drastically with increased inference throughput by scaling engines from 1 → 3 (**1200 → 661 s (≈1.8×)** in async mode). Sync mode runs rollouts and training sequentially on every step, so total step time speedups are less pronounced than the rollout throughput gains. At 3 engines, the queue is empty for most of the run, and peak queue depth is far lower (4 sync / 9 async).

*Numbers vary per run. Extracted metrics from all six runs are in [`benchmark/metrics.json`](https://github.com/skypilot-org/skypilot/blob/master/llm/slime/benchmark/metrics.json).*

## Components

- **Job Group (SkyPilot)** — gang-schedules the trainer + inference clusters as one
unit with stable inter-job hostnames.
- **slime, external mode** — Megatron trainer + N external SGLang engines.
- **Sandboxes (SkyPilot)** — warm pools of CPU pods that run the agent's untrusted code and tests.
- **[SWE-smith](https://github.com/SWE-bench/SWE-smith)** — real-repo bug-fixing dataset, a gold standard for agentic coding RL.
- **[mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent)** — a small, popular agent harness; drives the read → edit → run-tests loop.
- **Weight sync** — after each optimizer step the trainer publishes weights and the
engines reload, over a shared **RWX volume** (`disk`) or **NCCL**; `delta`
mode ships only the changed bytes.



## Further work

- **Autoscaling inference fleets** — the fleet is fixed per run today. For long-horizon rollouts with variable latency, inference engines should scale up and down with demand.
- **Pre-cached sandbox images** — pre-pull the repo images onto cluster nodes so many-image runs (the full multi-repo dataset, or `SANDBOX_NO_POOL=1`) don't pay a cold pull per claim.
- **Cross-cluster jobs** — place inference and training on separate clusters. Weight sync then needs an object-store carrier instead of a shared volume.
