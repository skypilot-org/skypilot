# Batch Audio Transcription with Hugging Face Storage

Transcribe a folder of audio files on any cloud, using Hugging Face Buckets as storage. Audio is read from one bucket and transcripts are written to another.

A [Hugging Face Bucket](https://huggingface.co/docs/hub/storage-buckets) is S3-like object storage on the Hub: mutable, non-versioned, and mountable as a filesystem from any machine. It is the working-storage counterpart to model and dataset repos — the place for inputs, outputs, and intermediate data rather than finished artifacts.

For this example we use a small speech-to-text model ([Cohere Transcribe](https://huggingface.co/CohereLabs/cohere-transcribe-03-2026), 2B) and an existing script from [`uv-scripts/transcription`](https://huggingface.co/datasets/uv-scripts/transcription), so the only new thing to read is the storage setup. The same script and the same two buckets work on Hugging Face Jobs, on SkyPilot on Lambda, and on SkyPilot on AWS, with no changes.

```
hf://buckets/<you>/skypilot-tutorial-audio
        │  mounted at /input
        ▼
  GPU on any cloud  (transcribe.yaml)
        │  writes to /output
        ▼
hf://buckets/<you>/skypilot-tutorial-transcripts
```

Files in this example:

- `transcribe.yaml`: the SkyPilot task — mounts the two buckets and runs the transcription script.

## Prerequisites

- SkyPilot with the Hugging Face extra and at least one cloud enabled:
  ```bash
  uv tool install --with pip "skypilot[huggingface,lambda]"   # or aws, gcp, ...
  sky check
  ```
- A Hugging Face account and token (`hf auth login`). SkyPilot forwards the token to the cloud VM, so bucket mounts authenticate with no extra setup.
- Accept the (auto-approved) terms on the [Cohere Transcribe model page](https://huggingface.co/CohereLabs/cohere-transcribe-03-2026) once.

> **Note:** Mounting requires `/dev/fuse` and glibc ≥ 2.34 on the VM — true on bare-VM clouds; on Kubernetes set a newer `image_id` (e.g. `docker:mirror.gcr.io/ubuntu:22.04`). Container-only clouds (e.g. RunPod) do not support object-store mounting in SkyPilot. See the [SkyPilot storage docs](https://docs.skypilot.co/en/latest/reference/storage.html) and the [Hugging Face storage integrations page](https://huggingface.co/docs/hub/storage-buckets-integrations#skypilot).

## Step 1: Put some audio in a bucket

Create the two buckets, then fill the input one. You can copy local files:

```bash
hf buckets create skypilot-tutorial-audio
hf buckets create skypilot-tutorial-transcripts
hf buckets cp ./my-audio/ hf://buckets/<you>/skypilot-tutorial-audio/
```

or, for something to try right away, pull ten episodes of a public-domain radio show from the Internet Archive with a CPU job on Hugging Face Jobs — the bucket is mounted there the same way (`-v`):

```bash
hf jobs uv run -v hf://buckets/<you>/skypilot-tutorial-audio:/output \
  https://huggingface.co/datasets/uv-scripts/transcription/raw/main/download-ia.py \
  SUSPENSE /output --max-files 10
```

## Step 2: Transcribe on a GPU, anywhere

```bash
export HF_TOKEN=$(hf auth whoami --token)   # or paste your token
sky launch -c transcribe transcribe.yaml --env HF_USER=<you> --secret HF_TOKEN
```

SkyPilot picks the cheapest available GPU across your enabled clouds. To pin one: `--infra lambda`, `--infra aws`, `--infra k8s`. The YAML does not change.

What happens on the VM:

1. Both buckets are FUSE-mounted (via [hf-mount](https://github.com/huggingface/hf-mount)) at `/input` and `/output`.
2. `uv` installs the script's pinned dependencies and downloads the model.
3. Each `/input/*.mp3` becomes `/output/*.txt`. Writes go straight to the Hub — open the transcripts bucket in another tab and watch them appear.

Measured on a Lambda 1× A10: ten episodes (295 minutes of audio) transcribed in 1.7 minutes — 173× realtime — and about 4 minutes wall-clock from `sky launch` to `Job finished`, including provisioning, dependency install, and the 2B model download.

```bash
sky down transcribe          # tear down; the transcripts are already on the Hub
hf buckets ls <you>/skypilot-tutorial-transcripts
```

## Going further

- **Managed job**: `sky jobs launch transcribe.yaml ...` runs the same task with automatic recovery if the VM is preempted or lost. Transcripts already written to the bucket survive; the recovered job re-runs the script from the start.
- **Larger collections**: one bucket pair per collection or language; point the two mounts at them and launch again. To spread a single bucket across several GPUs, add a shard argument to the script and use [SkyPilot Pools](../pools_batch_inference/); each worker mounts the same two buckets.
