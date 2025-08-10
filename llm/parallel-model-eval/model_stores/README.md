# Model Stores

This folder creates custom model stores for the multi-model evaluation example.

## What It Does

Sets up models from different sources to demonstrate SkyPilot's storage flexibility:
- **Kubernetes Volume**: Downloads Llama-3.2-1B to persistent volume storage
- **S3 Bucket**: Downloads Qwen2.5-1.5B to cloud object storage

## Quick Setup

```bash
# 1. Create Kubernetes volume (one-time setup)
sky volumes apply create-volume.yaml

# 2. Setup models
sky launch setup-volume.yaml -c setup --down -y --env HF_TOKEN
sky launch setup-s3-model.yaml -c setup-s3 --down -y  # Optional: S3 setup
```

## Using Your Own Fine-tuned Models

To use your own fine-tuned models in the evaluation:

### Option 1: Save to Volume
```yaml
# In your training task YAML
file_mounts:
  /volumes/model-checkpoints: volume://model-checkpoints

run: |
  # Your training script saves model directly to:
  python train.py --output /volumes/model-checkpoints/my-model
```

### Option 2: Save to S3/GCS Bucket
```yaml
# In your training task YAML  
file_mounts:
  /buckets/my-models: s3://my-models

run: |
  # Your training script saves model directly to:
  python train.py --output /buckets/my-models/my-model
```

### Use in Evaluation
Add to `configs/models_config.yaml`:
```yaml
models:
  - name: "my-finetuned-model"
    source: "volume://model-checkpoints/my-model"  # or s3://my-models/my-model
    accelerators: "L4:1"
```

## Key Benefits

- **No manual uploads**: SkyPilot automatically mounts storage, so training scripts can save directly
- **Persistent storage**: Models remain available across cluster restarts
- **Fast loading**: Volumes provide low-latency access for frequently used models

## Volume Management

```bash
# List volumes
sky volumes ls

# Delete when no longer needed
sky volumes delete model-checkpoints
```
