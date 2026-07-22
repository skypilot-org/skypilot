# Running slime RL post-training with SkyPilot

[**slime**](https://github.com/THUDM/slime) is THUDM's RL post-training framework
for LLMs, the system they use to post-train the **GLM** models. It connects
three components over [Ray](https://www.ray.io/):

- **Training**: [Megatron-LM](https://github.com/NVIDIA/Megatron-LM) owns the
  policy weights and runs the optimizer step.
- **Rollout**: [SGLang](https://github.com/sgl-project/sglang) generates samples
  and computes rewards.
- **Data buffer**: bridges prompts and rollouts between the two.

slime ships a Docker image and `ray start` + `ray job submit` launch scripts, but
it assumes you bring up the Ray cluster yourself. This example provisions the
nodes, wires the Ray cluster across them, and submits the job with a single
`sky launch`, on any cloud, Kubernetes, or on-prem cluster.

## What it runs

slime's canonical **Qwen3-4B GRPO** recipe (a direct port of
[`scripts/run-qwen3-4B.sh`](https://github.com/THUDM/slime/blob/main/scripts/run-qwen3-4B.sh)),
training on [dapo-math-17k](https://huggingface.co/datasets/zhuzilin/dapo-math-17k)
and evaluating on [aime-2024](https://huggingface.co/datasets/zhuzilin/aime-2024),
in slime's **colocate** mode: training (Megatron) and rollout (SGLang) share the
same GPUs, offloading sequentially, so a full RL loop fits on each node.

The model and datasets are downloaded from the Hugging Face Hub to local disk,
and checkpoints are written to a cloud bucket
([`s3://`](https://docs.skypilot.co/en/latest/reference/storage.html)) so they
persist past teardown.

## Prerequisites

- A SkyPilot-configured cluster with **8× H100** (or H200) GPUs per node
  (`sky check`, `sky show-gpus`).
- A **Hugging Face token** (`HF_TOKEN`) to read the model and datasets.

## Usage

```bash
# HF_TOKEN is used to read the model + datasets from Hugging Face.
sky launch -c slime llm/slime/slime.yaml --secret HF_TOKEN

sky logs slime    # stream training logs
sky down slime    # tear down
```

Add `--infra <cloud>` (e.g. `--infra k8s`, `--infra nebius`, `--infra aws`) to
target specific infrastructure.

### Multiple nodes

Pass `--num-nodes N` to scale out. SkyPilot starts the Ray head on the first node
and joins the rest as workers; all GPUs across all nodes run Megatron + SGLang in
colocate mode.

```bash
sky launch -c slime llm/slime/slime.yaml --secret HF_TOKEN --num-nodes 2  # 16 GPUs
```

## How it works

slime's launch scripts do `ray start --head` then `ray job submit ... train.py`.
The YAML's `setup` downloads the HF checkpoint and converts it to Megatron's
`torch_dist` format (`tools/convert_hf_to_torch_dist.py`); the `run` block starts
Ray and submits `train.py` with slime's argument groups exactly as the reference
script does. One adaptation makes it coexist with SkyPilot:

> **Ray co-existence.** SkyPilot manages its own Ray on port `6380`. slime's Ray
> runs on `6379` with its own `--temp-dir` and non-default dashboard-agent /
> metrics ports (52366-52369), so the two Ray instances sharing the node don't
> collide; otherwise job submission fails with *"No available agent to submit
> job"*. We `unset RAY_ADDRESS` so slime's CLI talks to slime's Ray, and skip
> slime's usual `ray stop` / `pkill ray`, which would kill SkyPilot's Ray.

## Configuration

Override any environment variable at launch with `--env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `HF_MODEL_REPO` | `Qwen/Qwen3-4B` | HF model repo to download |
| `MODEL_DIR` | `Qwen3-4B` | Local dir name under `/models` |
| `MODEL_SCRIPT` | `qwen3-4B` | slime model config in `scripts/models/` (must match the model) |
| `NUM_ROLLOUT` | `30` | Number of rollout→train iterations (slime's full recipe uses 3000) |

To switch models, set `HF_MODEL_REPO`, `MODEL_DIR`, and `MODEL_SCRIPT` together,
the last to the matching config under
[`scripts/models/`](https://github.com/THUDM/slime/tree/main/scripts/models)
(Qwen2.5/Qwen3, GLM-4, Llama-3, DeepSeek, …).

> **Checkpoints.** slime saves to `/checkpoints/<MODEL_DIR>_slime`, a cloud bucket
> mounted read-write via `file_mounts`, so checkpoints persist past teardown and
> are shared across nodes. Point it at your own bucket by editing the `source`
> (`s3://`, `gs://`, `r2://`, …); SkyPilot creates it if it doesn't exist.

## References

- [slime](https://github.com/THUDM/slime): THUDM's RL post-training framework
- [slime quick start](https://github.com/THUDM/slime/blob/main/docs/en/get_started/quick_start.md)
  · [Qwen3-4B example](https://github.com/THUDM/slime/blob/main/docs/en/examples/qwen3-4B.md)
- [GRPO paper](https://arxiv.org/abs/2402.03300) · [SkyPilot docs](https://docs.skypilot.co/)
