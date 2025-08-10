# Fine-tuning Models for Evaluation

This directory contains examples of fine-tuning models and saving them to either S3 buckets or SkyPilot volumes for use in multi-model evaluation.

## Overview

The workflow demonstrates:
1. Fine-tuning a base model from HuggingFace
2. Saving directly to mounted cloud storage (S3/GCS) or SkyPilot volumes
3. Using the saved models in multi-model evaluation

**Key Feature**: SkyPilot automatically mounts S3/GCS buckets and volumes, so the fine-tuning script can save models directly without manual uploads or AWS CLI.


## Files

- `finetune.py` - The fine-tuning script that handles both S3 and volume outputs
- `finetune-to-s3.yaml` - SkyPilot task for fine-tuning and saving to S3
- `finetune-to-volume.yaml` - SkyPilot task for fine-tuning and saving to a volume
- `create-volume.yaml` - Volume definition for Kubernetes clusters

## Quick Start

### Save to S3:
```bash
# 1. Edit finetune-to-s3.yaml - change "my-models" to your bucket
# 2. Launch
sky launch finetune-to-s3.yaml
```

### Save to Volume:
```bash
# 1. Create volume (one-time)
sky volumes apply create-volume.yaml

# 2. Launch
sky launch finetune-to-volume.yaml
```

That's it! The models are automatically saved to cloud storage.

## Using Fine-tuned Models in Evaluation

After fine-tuning, the models are ready to use in evaluation:

Just add to your `models_config.yaml`:

```yaml
models:
  - name: "agent-qwen"
    source: "s3://my-models/qwen-agent"  # or volume://model-checkpoints/qwen-agent
    accelerators: "L4:1"
```

See `example-usage.yaml` for a complete example.

Then run the evaluation:
```bash
cd ../..  # Back to multi-model-eval root
python evaluate_models.py
```

## Customization

### Change the Base Model

Edit the `BASE_MODEL` environment variable:
```yaml
envs:
  BASE_MODEL: Qwen/Qwen2.5-7B-Instruct  # Full size model
  # or
  BASE_MODEL: mistralai/Mistral-7B-Instruct-v0.3
  # or  
  BASE_MODEL: deepseek-ai/DeepSeek-R1-Distill-Llama-7B
```

### Adjust Training Parameters

Modify in the YAML files:
```yaml
envs:
  NUM_EPOCHS: 3  # More epochs for better fine-tuning
```

Or edit `finetune.py` for more control over:
- Learning rate
- Batch size
- Dataset selection
- Training arguments

### Use Custom Datasets

The default script uses IMDB for demonstration. To use your own dataset:

1. **From HuggingFace Hub**:
   ```python
   dataset = load_dataset("your-org/your-dataset", split="train")
   ```

2. **From local files** (mount them first):
   ```yaml
   # In your YAML file
   file_mounts:
     /data: ./my-training-data
   ```
   Then in `finetune.py`:
   ```python
   dataset = load_dataset("json", data_files="/data/*.json")
   ```

## Tips

1. **For production**: 
   - Use larger models and more epochs
   - Consider using QLoRA for efficient fine-tuning
   - Add validation set and metrics

2. **Cost optimization**:
   - Use spot instances: `sky launch --use-spot ...`
   - Use T4 GPUs for smaller models
   - Store frequently used models in volumes for faster access

3. **Debugging**:
   - Check logs: `sky logs <cluster-name>`
   - SSH into cluster: `sky exec <cluster-name> bash`
   - Test locally first with small data

## Volume Management

List volumes:
```bash
sky volumes ls
```

Delete volume when no longer needed:
```bash
sky volumes delete model-checkpoints
```

## Storage Configuration

### S3 Setup
When using S3, SkyPilot handles authentication automatically if you have AWS credentials configured. The bucket will be mounted at `/buckets/agent-models/` (or your chosen mount path).

### Volume Setup  
For Kubernetes volumes:
- GKE uses `standard` storage class by default
- Volumes persist across cluster restarts
- Use `sky volumes ls` to manage volumes

### Direct Saving
The fine-tuning script saves models directly to mounted paths without needing AWS CLI or manual uploads. SkyPilot handles all the mounting automatically.
