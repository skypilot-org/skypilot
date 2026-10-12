# RL Post-Training with vime + SkyPilot Job Groups

This example runs [**vime**](https://github.com/vllm-project/vime) (an LLM RL
post-training framework from the vLLM project that pairs **vLLM** rollout with a
**Megatron** training backend) on top of **SkyPilot Job Groups**.

vime is built on [Ray](https://www.ray.io/) and runs in two placement modes:

- **Colocated**: training and rollout share the same GPUs.
- **Disaggregated**: training (Megatron) and rollout (vLLM) run on *separate*
  GPUs, in parallel.

Disaggregated mode is where Job Groups shine: each RL component becomes its own
task with its own resources, and SkyPilot wires them together automatically.

```
                 SkyPilot Job Group: "vime-grpo"
   ┌─────────────────────────────┐     ┌─────────────────────────────┐
   │  vime-trainer  (primary)    │     │  vime-rollout  (auxiliary)  │
   │  8 × H100                   │     │  8 × H100                   │
   │                             │     │                             │
   │  Ray head + GRPO driver     │◄───►│  Ray worker                 │
   │  Megatron actors (learner)  │     │  vLLM engines + router      │
   └─────────────┬───────────────┘     └──────────────┬──────────────┘
                 │   policy weights (NCCL) ──────────► │
                 │ ◄──────────── rollouts / log-probs  │
                 └──────── one Ray cluster over ───────┘
                          Job Group DNS
```

## Architecture

Two tasks run in parallel as a single managed Job Group:

1. **vime-rollout** (auxiliary, 8×H100): joins the trainer's Ray cluster and
   contributes its GPUs to host the **vLLM rollout engines**. vime's driver
   places the engines here; the task itself prepares the model (see below),
   joins Ray, and stays alive until training finishes.

2. **vime-trainer** (primary, 8×H100): starts the Ray head, waits for the
   rollout worker to join, and submits the vime GRPO job. vime runs the
   **Megatron actors** (learner) on one task's GPUs and the **vLLM engines** on
   the other's.

Both GPU tasks download the model and convert it to Megatron `torch_dist` format
in `setup`. vime places the Megatron actor group on one of the two tasks (the
choice is symmetric), so both need the converted checkpoint available locally.
This is what lets the example run without a shared filesystem.

### How the components are distributed

vime launches a single Ray cluster (`ray start --head` + `ray job submit
train.py`) and decides GPU placement from its arguments. Two things make the
disaggregation map cleanly onto Job Group tasks:

- The **vime-trainer** task starts the Ray head; the **vime-rollout** task joins
  it as a Ray worker over the Job Group network
  (`vime-trainer-0.${SKYPILOT_JOBGROUP_NAME}:6379`).
- Training is launched with `--actor-num-gpus-per-node 8 --rollout-num-gpus 8`
  and **without** `--colocate`, so vime keeps the 8 training GPUs and the 8
  rollout GPUs separate, i.e. on the two separate tasks/pods.

Each task downloads its own copy of the model, so **no shared filesystem is
required**: the trainer reads it for Megatron, the rollout reads it to start
vLLM, and the two sync policy weights over Ray/NCCL rather than over disk.

> **Ray co-existence note.** SkyPilot manages its own Ray instance on port
> `6380`; vime's Ray runs on `6379` with its own `--temp-dir`, so the two do not
> collide. (This is why the example does **not** run vime's usual
> `ray stop --force` / `pkill ray` lines, which would kill SkyPilot's Ray.)

### Primary / auxiliary lifecycle

`vime-trainer` is the **primary** task. When training completes, the
`vime-rollout` auxiliary task is terminated automatically after a 30s grace
period (`termination_delay: 30s`), releasing its GPUs promptly.

## Usage

```bash
sky jobs launch llm/vime-jobgroup/vime-grpo-jobgroup.yaml
```

The default model and datasets are public, so no token is needed. To use a
Hugging Face token (recommended for the large 4B download, required for gated
models), set it locally and forward it:

```bash
export HF_TOKEN=...
sky jobs launch llm/vime-jobgroup/vime-grpo-jobgroup.yaml --env HF_TOKEN
```

This trains **Qwen3-4B** with GRPO on the
[dapo-math-17k](https://huggingface.co/datasets/zhuzilin/dapo-math-17k) dataset
(verifiable math rewards), evaluating on
[aime-2024](https://huggingface.co/datasets/zhuzilin/aime-2024).

### Monitor

```bash
# Status of all tasks in the group
sky jobs queue

# Logs for a specific task
sky jobs logs <job-id> vime-trainer
sky jobs logs <job-id> vime-rollout
```

## Configuration

Override any of the task environment variables at launch with `--env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `HF_MODEL_REPO` | `Qwen/Qwen3-4B` | HF model repo to download |
| `MODEL_DIR` | `Qwen3-4B` | Local dir under `/models` |
| `VIME_MODEL_SCRIPT` | `qwen3-4B` | vime model config in `scripts/models/` |
| `NUM_ROLLOUT` | `30` | Number of rollout→train iterations |

For example, a quick smoke test with a smaller model:

```bash
sky jobs launch llm/vime-jobgroup/vime-grpo-jobgroup.yaml \
  --env HF_MODEL_REPO=Qwen/Qwen3-0.6B \
  --env MODEL_DIR=Qwen3-0.6B \
  --env VIME_MODEL_SCRIPT=qwen3-0.6B \
  --env NUM_ROLLOUT=2
```

> **Checkpoints.** vime writes checkpoints (`--save`) to
> `/models/<MODEL_DIR>_vime` on the pod hosting the Megatron actors, which is
> ephemeral. Two ways to persist them:
>
> - **SkyPilot volume:** mount a
>   [volume](https://docs.skypilot.co/en/latest/reference/volumes.html) (or any
>   `ReadWriteOnce` PVC) and point `--save` at it.
> - **Hugging Face Storage bucket:** mount an `hf://` Bucket and save there. It
>   persists to the Hub with no egress, and (unlike a `ReadWriteOnce` volume) the
>   same bucket mounts on both GPU tasks at once, so the checkpoint survives
>   wherever vime places the actor group:
>
>   ```yaml
>   file_mounts:
>     /checkpoints:
>       source: hf://buckets/<namespace>/vime-grpo
>       store: hf
>       mode: MOUNT
>   ```
>
>   Then point `--save` (and `--load`, to resume across runs) at
>   `/checkpoints/...`, add the extra (`pip install "skypilot[huggingface]"`), and
>   pass your token with `--secret HF_TOKEN`. See the [storage
>   docs](https://docs.skypilot.co/en/latest/reference/storage.html).

### Scaling

- **More rollout throughput**: increase the `vime-rollout` GPU count and
  `--rollout-num-gpus`; vime's vLLM router load-balances across the extra
  engines (`dp_size = rollout-num-gpus / rollout-num-gpus-per-engine`).
- **Larger models / longer training**: bump the trainer GPU count and the
  Megatron parallelism (`--tensor-model-parallel-size`,
  `--pipeline-model-parallel-size`, `--context-parallel-size`).

## References

- [vime](https://github.com/vllm-project/vime): vLLM RL post-training framework
- [vime quick start](https://github.com/vllm-project/vime/blob/main/docs/en/get_started/quick_start.md)
- [SkyPilot Job Groups](https://docs.skypilot.co/en/latest/examples/job-groups.html)
- [GRPO paper](https://arxiv.org/abs/2402.03300)
