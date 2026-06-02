# Fine-tuning NVIDIA Cosmos 3 with SkyPilot

[NVIDIA Cosmos 3](https://developer.nvidia.com/blog/develop-physical-ai-reasoning-world-and-action-models-with-nvidia-cosmos-3/)
is a family of open **omnimodal world foundation models** for Physical AI —
robotics, autonomous vehicles, and smart spaces. Built on a Mixture-of-Transformers
architecture, a Cosmos 3 model pairs a **reasoner** tower (a vision-language model
over text/image/video/audio/action) with a **generator** tower (a diffusion model
that synthesizes future video/image/action). This example fine-tunes the smallest
member, `Cosmos3-Nano` (16B), on a single SkyPilot cluster.

| Model | Params | Notes |
| ----- | ------ | ----- |
| [`nvidia/Cosmos3-Nano`](https://huggingface.co/nvidia/Cosmos3-Nano)   | 16B | Workstation/efficient tier — used in this example |
| [`nvidia/Cosmos3-Super`](https://huggingface.co/nvidia/Cosmos3-Super) | 64B | Datacenter / frontier tier |
| [`nvidia/Cosmos3-Super-Image2Video`](https://huggingface.co/nvidia/Cosmos3-Super-Image2Video) | 64B | Image-to-video specialization |

It runs NVIDIA's official supervised fine-tuning recipe
[`vision_sft_nano`](https://github.com/NVIDIA/cosmos-framework/blob/main/examples/launch_sft_vision_nano.sh)
from [cosmos-framework](https://github.com/NVIDIA/cosmos-framework) — which
post-trains the Cosmos3-Nano generation pathway (text/image/video → video) with
FSDP across 8 GPUs in bfloat16 — on
[`nvidia/bridge-v2-subset-synthetic-captions`](https://huggingface.co/datasets/nvidia/bridge-v2-subset-synthetic-captions),
a ~650 MB subset of [BridgeData V2](https://rail-berkeley.github.io/bridgedata/)
robot-manipulation videos with synthetic captions. This is the dataset NVIDIA
ships with the recipe, so it cleanly exercises the full pipeline: download →
checkpoint conversion → training → checkpoint → safetensors export.

To fine-tune on your own data, point the launcher at a dataset laid out like
`train/video_dataset_file.jsonl` by setting its `DATASET_PATH` in the `run` block
(see [`docs/dataset_jsonl.md`](https://github.com/NVIDIA/cosmos-framework/blob/main/docs/dataset_jsonl.md)).

## Run it

```bash
# Full reference recipe (max_iter=500). The model + dataset are public, so no token is needed.
sky launch -c cosmos3 examples/cosmos3-finetuning/cosmos3_nano_finetune.yaml
```

SkyPilot picks the cheapest cloud/region with 8× H100/H200; add `--infra nebius`
(or `aws`, `gcp`, …) to pin one. To authenticate to Hugging Face (e.g. to avoid
download rate limits), `export HF_TOKEN=...` and add `--secret HF_TOKEN`.

The first launch provisions the cluster and runs `setup` (install env, download
the dataset + Wan2.2 VAE, download Cosmos3-Nano and convert it to DCP format),
then `run` (train + export). Setup downloads ~35 GB (NGC image, CUDA-13 deps, the
16B model, VAE, dataset), so it can take 30+ minutes and looks idle while the
quiet downloads run. Subsequent runs reuse the cached setup.

**Quick smoke test** — run a handful of steps to verify the whole pipeline
cheaply; it still produces a real checkpoint and safetensors export:

```bash
sky launch -c cosmos3 examples/cosmos3-finetuning/cosmos3_nano_finetune.yaml \
    --env MAX_ITER=10 --env SAVE_ITER=5
```

`setup` is cached, so you can iterate on training alone with
`sky exec cosmos3 examples/cosmos3-finetuning/cosmos3_nano_finetune.yaml --env MAX_ITER=200`.

### Tunable knobs (`--env`)

| Env var | Default | Meaning |
| ------- | ------- | ------- |
| `MAX_ITER` | `500` | Number of optimizer steps (set small for a smoke test). |
| `SAVE_ITER` | `100` | Save a DCP checkpoint every N steps. |
| `BASE_CHECKPOINT_NAME` | `Cosmos3-Nano` | Base checkpoint to fine-tune (catalog name). |
| `EXPORT_SAFETENSORS` | `1` | Export the trained checkpoint to HF safetensors. |
| `COSMOS_FRAMEWORK_REF` | pinned commit | cosmos-framework git ref to install. |

## Outputs

Training writes to `~/cosmos-framework/outputs/train/cosmos3/sft/vision_sft_nano/`:

- `checkpoints/iter_<N>/` — sharded DCP checkpoints.
- `model/` — the exported Hugging Face safetensors (when `EXPORT_SAFETENSORS=1`).

Fetch them with `ssh cosmos3` or `rsync`. Tear down with `sky down cosmos3`.

## References

- Cosmos 3 blog: https://developer.nvidia.com/blog/develop-physical-ai-reasoning-world-and-action-models-with-nvidia-cosmos-3/
- Technical report: https://research.nvidia.com/labs/cosmos-lab/cosmos3/technical-report.pdf
- cosmos-framework (training/serving): https://github.com/NVIDIA/cosmos-framework
- NVIDIA Cosmos (models, inference): https://github.com/NVIDIA/Cosmos
