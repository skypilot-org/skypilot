# Fine-tuning NVIDIA Cosmos 3 with SkyPilot

[NVIDIA Cosmos 3](https://developer.nvidia.com/blog/develop-physical-ai-reasoning-world-and-action-models-with-nvidia-cosmos-3/)
is a family of open **omnimodal world foundation models** for Physical AI:
robotics, autonomous vehicles, and smart spaces. Built on a Mixture-of-Transformers
architecture, a Cosmos 3 model pairs a **reasoner** tower (a vision-language model
over text/image/video/audio/action) with a **generator** tower (a diffusion model
that synthesizes future video/image/action). This example fine-tunes the smallest
member, `Cosmos3-Nano` (16B), as a SkyPilot managed job with checkpoint-to-bucket
auto-recovery.

| Model | Params | Notes |
| ----- | ------ | ----- |
| [`nvidia/Cosmos3-Nano`](https://huggingface.co/nvidia/Cosmos3-Nano)   | 16B | Workstation/efficient tier, used in this example |
| [`nvidia/Cosmos3-Super`](https://huggingface.co/nvidia/Cosmos3-Super) | 64B | Datacenter / frontier tier |
| [`nvidia/Cosmos3-Super-Image2Video`](https://huggingface.co/nvidia/Cosmos3-Super-Image2Video) | 64B | Image-to-video specialization |

It runs NVIDIA's official supervised fine-tuning recipe
[`vision_sft_nano`](https://github.com/NVIDIA/cosmos-framework/blob/main/examples/launch_sft_vision_nano.sh)
from [cosmos-framework](https://github.com/NVIDIA/cosmos-framework): it
post-trains the Cosmos3-Nano generation pathway (text/image/video → video) with
FSDP across 8 GPUs in bfloat16. It trains on
[`nvidia/bridge-v2-subset-synthetic-captions`](https://huggingface.co/datasets/nvidia/bridge-v2-subset-synthetic-captions),
a ~650 MB subset of [BridgeData V2](https://rail-berkeley.github.io/bridgedata/)
robot-manipulation videos. To fine-tune on your own data, make it available on the
instance (e.g. via another `file_mounts` bucket) laid out like
`train/video_dataset_file.jsonl` and pass `--env DATASET_PATH=/path/to/it` (see
[`docs/dataset_jsonl.md`](https://github.com/NVIDIA/cosmos-framework/blob/main/docs/dataset_jsonl.md)).

## Run it

```bash
pip install "skypilot-nightly[aws,gcp,kubernetes]"  # pick your clouds
sky check
```

Pick a globally-unique `CHECKPOINT_BUCKET_NAME`. SkyPilot creates the bucket and
mounts it at `/checkpoints` (the recipe's `OUTPUT_ROOT`), so the job auto-resumes
from the latest checkpoint after a recovery and the outputs outlive its cluster.

```bash
sky jobs launch -n cosmos3 examples/cosmos3-finetuning/cosmos3_nano_finetune.yaml \
    --env CHECKPOINT_BUCKET_NAME=my-cosmos3-checkpoints
```

SkyPilot picks the cheapest cloud/region with 8× H100/H200; add `--infra <cloud>`
(e.g. `aws`, `gcp`, `k8s`) to pin one, or `--use-spot` for cheaper preemptible GPUs
(the job auto-resumes after a preemption). The model and dataset are public, so no token is needed; for HF auth,
`export HF_TOKEN=...` and add `--secret HF_TOKEN`. The first run downloads ~35 GB in
`setup` (base model + VAE + dataset; 30+ min, and looks idle during the quiet
downloads), then trains + exports.

**Smoke test** (a few steps to exercise the whole pipeline, still checkpoints + exports):

```bash
sky jobs launch -n cosmos3 examples/cosmos3-finetuning/cosmos3_nano_finetune.yaml \
    --env CHECKPOINT_BUCKET_NAME=my-cosmos3-checkpoints --env MAX_ITER=10 --env SAVE_ITER=5
```

Monitor and manage it:

```bash
sky jobs queue            # status of all managed jobs
sky jobs logs -n cosmos3  # stream logs
sky jobs cancel -n cosmos3
```

### Tunable knobs (`--env`)

| Env var | Default | Meaning |
| ------- | ------- | ------- |
| `CHECKPOINT_BUCKET_NAME` | `my-cosmos3-checkpoints` | Globally-unique cloud bucket for checkpoints + auto-resume (change to your own). |
| `DATASET_PATH` | bridge subset | Dataset dir the launcher trains on (override for your own data). |
| `MAX_ITER` | `500` | Number of optimizer steps (set small for a smoke test). |
| `SAVE_ITER` | `100` | Save a DCP checkpoint every N steps. |
| `EXPORT_SAFETENSORS` | `1` | Export the trained checkpoint to HF safetensors. |
| `COSMOS_FRAMEWORK_REF` | pinned commit | cosmos-framework git ref to install. |

## Outputs

Checkpoints (`checkpoints/iter_<N>/`), the resolved `config.yaml`, and the exported
safetensors (`model/`) land in your bucket under `cosmos3/sft/vision_sft_nano/`. Run
`sky storage ls` to find the bucket, then download with that cloud's CLI, e.g.
`aws s3 sync s3://my-cosmos3-checkpoints/ .`.

## References

- Cosmos 3 blog: https://developer.nvidia.com/blog/develop-physical-ai-reasoning-world-and-action-models-with-nvidia-cosmos-3/
- Technical report: https://research.nvidia.com/labs/cosmos-lab/cosmos3/technical-report.pdf
- cosmos-framework: https://github.com/NVIDIA/cosmos-framework
- NVIDIA Cosmos: https://github.com/NVIDIA/Cosmos
