# Batch Image Generation with Stable Diffusion

This example generates images from text prompts using Stable Diffusion v1.5, distributed across a pool of GPU workers via Sky Batch.

## What it does

Takes a JSONL file of text prompts and generates one PNG image per prompt.

Input (`prompts.jsonl`):
```json
{"prompt": "a photo of an astronaut riding a horse on mars"}
{"prompt": "a beautiful sunset over the ocean, oil painting"}
```

Output (`generated_images/`):
```
generated_images/
  00000000.png        # astronaut image
  00000001.png        # sunset image
  manifest.jsonl      # maps prompts to filenames
```

Where `manifest.jsonl` contains:
```json
{"prompt": "a photo of an astronaut riding a horse on mars", "image": "00000000.png"}
{"prompt": "a beautiful sunset over the ocean, oil painting", "image": "00000001.png"}
```

## Prerequisites

1. AWS CLI installed and credentials configured (`aws configure`)
2. SkyPilot installed with AWS support: `pip install "skypilot[aws]"`

## Steps

All commands are run from the **project root**.

### 1. Prepare S3 bucket and prompts

The `prepare.sh` script creates an S3 bucket, generates sample prompts,
and uploads them:

```bash
# Auto-generate a unique bucket name
bash examples/batch/diffusion/prepare.sh

# Or specify your own bucket name
bash examples/batch/diffusion/prepare.sh my-diffusion-bucket
```

This will:
- Create an S3 bucket (default region: `us-east-1`, override with `AWS_DEFAULT_REGION`)
- Write 9 sample prompts to `prompts.jsonl`
- Upload to `s3://<bucket>/prompts.jsonl`

To use your own prompts instead, create a JSONL file with one `{"prompt": "..."}` per line:

```bash
cat > /tmp/prompts.jsonl << 'EOF'
{"prompt": "a photo of an astronaut riding a horse on mars"}
{"prompt": "a beautiful sunset over the ocean, oil painting"}
EOF
aws s3 cp /tmp/prompts.jsonl s3://$SKY_BATCH_BUCKET/prompts.jsonl
```

### 2. Run

```bash
export SKY_BATCH_BUCKET=<bucket name printed by prepare.sh>
python examples/batch/diffusion/generate_images.py
```

This will:
1. Create a pool of 2 AWS L4 GPU workers (if not already running)
2. Load Stable Diffusion v1.5 on each worker (once)
3. Distribute prompts across workers in batches of 3
4. Save generated images as PNG files to S3
5. Create a `manifest.jsonl` mapping prompts to image filenames

### 3. Download results

```bash
# Download all images and manifest
aws s3 cp s3://$SKY_BATCH_BUCKET/generated_images/ ./generated_images/ --recursive

# View the manifest
cat generated_images/manifest.jsonl
```

### 4. Clean up

```bash
# Tear down the pool
sky jobs pool down diffusion-pool -y

# Remove the S3 bucket
aws s3 rb s3://$SKY_BATCH_BUCKET --force
```

## Pool Configuration

The `pool.yaml` uses AWS L4 GPUs:

```yaml
pool:
  workers: 2

resources:
  cloud: aws
  accelerators: L4:1

setup: |
  uv venv .venv --seed --python 3.11
  source .venv/bin/activate
  uv pip install torch torchvision diffusers transformers accelerate safetensors
```

You can modify:
- `workers`: More workers = faster processing (each loads its own model copy)
- `accelerators`: Use `A10G:1` or `A100:1` for faster generation

## Environment Activation

The pool `setup` installs dependencies into a virtual environment
(`.venv`). The mapper function must run in the same environment, so
`ds.map()` takes an `activate_env` argument:

```python
ds.map(
    generate_images,
    pool_name=pool_name,
    batch_size=3,
    output_path=output_path,
    activate_env='source .venv/bin/activate',  # must match pool setup
)
```

This shell snippet runs before the worker process starts, so `python`
and all installed packages resolve to the virtual environment.

## How the Image Output Works

Sky Batch detects the output format from the path:
- Path ending with `.jsonl` → JSONL output (text data)
- Path ending with `/` → Image directory output

When using image directory output, `sky.batch.save_results()` looks for
PIL Image objects in the result dicts. Each image is uploaded as a
separate PNG file (`{index:08d}.png`), and a `manifest.jsonl` is created
to track the mapping between inputs and output files.

## Architecture

```
User Machine                   Controller Cluster             Worker Nodes (L4 GPU)
generate_images.py             BatchController (:8280)
  ds.map() ── HTTP ──────────►   ├─ Count prompts (6)
    POST /submit_job              ├─ Create batches ([0-2], [3-5])
    GET  /job_status              ├─ Discover 2 workers
                                  ├─ Worker 0 thread:
                                  │   sky.exec(start_worker)  ──► Load SD model
                                  │   sky.exec(feed batch 0)  ──► Generate 3 images
                                  │   sky.exec(feed batch 1)  ──► Generate 3 images
                                  │   sky.exec(shutdown)
                                  ├─ Worker 1 thread:
                                  │   sky.exec(start_worker)  ──► Load SD model
                                  │   sky.exec(feed batch 0)  ──► (grabbed by W0)
                                  │   ...
                                  └─ Merge manifest.jsonl
```
