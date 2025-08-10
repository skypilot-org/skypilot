# Multi-Model Evaluation Demo

This folder contains everything needed to demo SkyPilot's multi-model evaluation capabilities.

## Prerequisites
- HuggingFace token with Llama access (set as `HF_TOKEN` environment variable)
- Access to GKE cluster
- AWS credentials for S3 demo

## Quick Start

### Automated Setup
```bash
# Run complete setup (from multi-model-eval directory)
./demo/setup_demo_complete.sh zhwu-api-test us-central1-c
```

### Manual Setup
```bash
# 1. Connect to GKE
gcloud container clusters get-credentials zhwu-api-test --region us-central1-c

# 2. Create Kubernetes volume
sky volumes apply finetuning/create-volume.yaml

# 3. Setup models from different sources
sky launch demo/setup-volume.yaml -c setup --down -y --env HF_TOKEN
sky launch demo/setup-s3-model.yaml -c setup-s3 --down -y

# 4. Run evaluation
python evaluate_models.py
```

## Demo Files

### Setup Scripts
- **`setup-volume.yaml`** - Downloads Llama-3.2-1B and saves to Kubernetes volume
- **`setup-s3-model.yaml`** - Downloads Qwen2.5-1.5B and saves to S3 bucket
- **`setup_demo_complete.sh`** - Automated setup script for entire demo

### Reference
- **`DEMO_COMMANDS.md`** - Quick command reference

## Demo Presentation Flow

### Part 1: Introduction (3 minutes)

**"Today I'll demonstrate how SkyPilot enables parallel evaluation of multiple LLMs, including fine-tuned models."**

Show the architecture:
- Parallel model deployment
- Support for HuggingFace, S3, and volume storage
- OpenAI-compatible APIs for easy integration

### Part 2: Show Model Sources (2 minutes)

**"Let me show you how SkyPilot handles different model sources."**

```bash
# Show the clean configuration
cat models_config.yaml

# Point out all three storage types:
# - HuggingFace: Mistral-7B (public cloud)
# - S3 bucket: Qwen2.5-1.5B (private cloud storage)
# - Volume: Llama-3.2-1B (persistent Kubernetes storage)
```

### Part 3: Multi-Model Evaluation (10 minutes)

**"Now let's evaluate these models in parallel."**

```bash
# Launch the evaluation
python evaluate_models.py

# While models are launching, explain:
# - Mistral-7B from HuggingFace (public)
# - Qwen2.5-1.5B from S3 (private cloud storage)
# - Llama-3.2-1B from volume (Kubernetes storage)
# - All launch in parallel - 3x faster!
# - Each gets its own GPU cluster

# Monitor status
sky status

# Check logs if needed
sky logs eval-mistral-7b --tail 20
```

### Part 4: Results & Business Value (5 minutes)

```bash
# View results
promptfoo view

# Show cluster status
sky status
```

**Key Benefits:**
1. **Speed**: 3 models evaluated in time of 1
2. **Flexibility**: Any model source works
3. **Cost Control**: Spot instances, auto-cleanup
4. **Enterprise Ready**: Private models, K8s support

## Key Demo Points

### Model Source Flexibility
- Public models (HuggingFace)
- Private models (S3 buckets)
- Persistent storage (Kubernetes volumes)

### Parallel Efficiency
- 3 models evaluated simultaneously
- Same cost, 3x faster results

### Simple Integration
- Just specify source in YAML
- SkyPilot handles all mounting/authentication
- OpenAI-compatible APIs

## Troubleshooting

```bash
# Debug specific cluster
sky logs eval-<model-name> --tail 100

# Force restart a model
sky down eval-<model-name>
sky launch templates/serve-model.yaml --cluster eval-<model-name>

# Check volume mount
sky exec eval-<model-name> "ls /volumes/"

# Cleanup all resources
sky down -a -y
```

## Post-Demo Follow-up

Suggest next steps:
1. Fine-tune on their proprietary data
2. Add more models to comparison
3. Integrate with their ML pipeline
4. Set up automated evaluation CI/CD