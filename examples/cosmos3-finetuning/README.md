# Fine-tuning NVIDIA Cosmos 3 with SkyPilot

[NVIDIA Cosmos 3](https://developer.nvidia.com/blog/develop-physical-ai-reasoning-world-and-action-models-with-nvidia-cosmos-3/)
is a family of open **omnimodal world foundation models** for Physical AI:
robotics, autonomous vehicles, and smart spaces. Built on a Mixture-of-Transformers
architecture, a Cosmos 3 model pairs a **reasoner** tower (a vision-language model
over text/image/video/audio/action) with a **generator** tower (a diffusion model
that synthesizes future video/image/action). This example fine-tunes the smallest
member, `Cosmos3-Nano` (16B), as a SkyPilot managed job on **Kubernetes**, with
checkpoints on a SkyPilot [volume](https://docs.skypilot.co/en/stable/reference/volumes.html)
for auto-recovery.

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
robot-manipulation videos. To fine-tune on your own data, mount it as a second
[volume](https://docs.skypilot.co/en/stable/reference/volumes.html) (or a
[cloud bucket](https://docs.skypilot.co/en/latest/reference/storage.html)), laid out like
`train/video_dataset_file.jsonl`, and pass `--env DATASET_PATH=/path/to/it` (see
[`docs/dataset_jsonl.md`](https://github.com/NVIDIA/cosmos-framework/blob/main/docs/dataset_jsonl.md)).

## Run it

You'll need a Kubernetes cluster with 8× H100/H200 GPUs. Point SkyPilot at it and verify:

```bash
pip install "skypilot-nightly[kubernetes]"
sky check k8s
```

### 1. Create the checkpoint volume

SkyPilot [volumes](https://docs.skypilot.co/en/stable/reference/volumes.html) are
Kubernetes PVCs with a lifecycle independent of any cluster — perfect for durable
checkpoints. Create one (mounted at `/checkpoints`, the recipe's `OUTPUT_ROOT`) so
the managed job auto-resumes from the latest checkpoint after a recovery and the
outputs outlive the job's cluster:

```bash
sky volumes apply examples/cosmos3-finetuning/cosmos3_checkpoints_volume.yaml
```

This creates a 1 Ti `cosmos3-checkpoints` PVC. See
[`cosmos3_checkpoints_volume.yaml`](cosmos3_checkpoints_volume.yaml) to set the size,
storage class, or access mode for your cluster.

### 2. Launch the fine-tuning job

```bash
sky jobs launch -n cosmos3 examples/cosmos3-finetuning/cosmos3_nano_finetune.yaml
```

SkyPilot schedules the job on a Kubernetes node with 8× H100/H200 and mounts the
`cosmos3-checkpoints` volume at `/checkpoints`. The model and dataset are public, so
no token is needed; for HF auth, `export HF_TOKEN=...` and add `--secret HF_TOKEN`.
The first run downloads ~35 GB in `setup` (base model + VAE + dataset; 30+ min, and
looks idle during the quiet downloads), then trains + exports.

> **Multiple Kubernetes clusters?** Pin one with `--infra k8s/<context>`. To run on a
> cloud instead, see *Using a cloud bucket* below.

**Smoke test** (a few steps to exercise the whole pipeline, still checkpoints + exports):

```bash
sky jobs launch -n cosmos3 examples/cosmos3-finetuning/cosmos3_nano_finetune.yaml \
    --env MAX_ITER=10 --env SAVE_ITER=5
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
| `DATASET_PATH` | bridge subset | Dataset dir the launcher trains on (override for your own data). |
| `MAX_ITER` | `500` | Number of optimizer steps (set small for a smoke test). |
| `SAVE_ITER` | `100` | Save a DCP checkpoint every N steps. |
| `EXPORT_SAFETENSORS` | `1` | Export the trained checkpoint to HF safetensors. |
| `COSMOS_FRAMEWORK_REF` | pinned commit | cosmos-framework git ref to install. |

## Outputs

Checkpoints (`checkpoints/iter_<N>/`), the resolved `config.yaml`, and the exported
safetensors (`model/`) land on the `cosmos3-checkpoints` volume under
`cosmos3/sft/vision_sft_nano/`. The volume persists after the job finishes — inspect
it with `sky volumes ls`, or mount it from another SkyPilot task (e.g. a serving job)
with a `volumes:` block to read the exported model.

## Bring your own dataset

Put your data on a second volume and point the recipe at it. Create the volume (laid
out with `train/video_dataset_file.jsonl`; see the
[dataset docs](https://github.com/NVIDIA/cosmos-framework/blob/main/docs/dataset_jsonl.md)),
then in `cosmos3_nano_finetune.yaml` uncomment the dataset mount under `volumes:`:

```yaml
volumes:
  /checkpoints: cosmos3-checkpoints
  /my-dataset: my-dataset-volume
```

and launch with `--env DATASET_PATH=/my-dataset`.

## Using a cloud bucket instead of a volume

Most of this example is Kubernetes + volume centric, but nothing requires it. To run
on a cloud (or to keep checkpoints in object storage for cross-region access), drop
the `volumes:` block in `cosmos3_nano_finetune.yaml` and mount a bucket at
`/checkpoints` instead:

```yaml
file_mounts:
  /checkpoints:
    name: my-cosmos3-checkpoints  # globally-unique bucket name; SkyPilot creates it
    mode: MOUNT
```

Then remove `infra: kubernetes` (or set `--infra <cloud>`) and SkyPilot picks the
cheapest cloud/region with 8× H100/H200. Add `--use-spot` for cheaper preemptible
GPUs — the job auto-resumes from the bucket after a preemption. See
[Cloud Buckets](https://docs.skypilot.co/en/latest/reference/storage.html).

## References

- Cosmos 3 blog: https://developer.nvidia.com/blog/develop-physical-ai-reasoning-world-and-action-models-with-nvidia-cosmos-3/
- Technical report: https://research.nvidia.com/labs/cosmos-lab/cosmos3/technical-report.pdf
- cosmos-framework: https://github.com/NVIDIA/cosmos-framework
- NVIDIA Cosmos: https://github.com/NVIDIA/Cosmos
- SkyPilot Volumes: https://docs.skypilot.co/en/stable/reference/volumes.html
