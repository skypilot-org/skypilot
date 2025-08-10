# Multi-Model Evaluation Demo Guide

## Prerequisites
- HuggingFace token with Llama access (set as `HF_TOKEN` environment variable)
- Access to GKE cluster
- S3 bucket named "my-models" (or update in models_config.yaml)

## Pre-Demo Setup (5 minutes)

```bash
# 1. Connect to GKE
gcloud container clusters get-credentials zhwu-api-test --region us-central1-c

# 2. Create volume and copy a model (for demo)
sky volumes apply finetuning/create-volume.yaml

# 3. Quick setup to populate volume with a model
cat > setup-volume.yaml << 'EOF'
resources:
  accelerators: L4:1
  disk_size: 50
workdir: .
volumes:
  /volumes: model-checkpoints
envs:
  HF_TOKEN: ${HF_TOKEN}  # Set your HuggingFace token
setup: |
  uv pip install transformers torch accelerate
run: |
  python -c "
  from transformers import AutoModelForCausalLM, AutoTokenizer
  model = AutoModelForCausalLM.from_pretrained('meta-llama/Llama-2-7b-hf', torch_dtype='auto')
  tokenizer = AutoTokenizer.from_pretrained('meta-llama/Llama-2-7b-hf') 
  model.save_pretrained('/volumes/agent-llama')
  tokenizer.save_pretrained('/volumes/agent-llama')
  print('✅ Llama-2-7B saved to volume')
  "
EOF

sky launch setup-volume.yaml -c setup --down
```

## Demo Flow

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

# Point out:
# - HuggingFace models (public)
# - S3 bucket models (private cloud storage)
# - Volume models (persistent Kubernetes storage)
```

### Part 3: Multi-Model Evaluation (10 minutes)

**"Now let's evaluate these models in parallel."**

```bash
# Launch the evaluation
python evaluate_models.py

# While models are launching, explain:
# - Mistral from HuggingFace (public)
# - Agent-Qwen from S3 (private cloud)
# - Agent-Llama from volume (Kubernetes storage)
# - All launch in parallel - 3x faster!
# - Each gets its own GPU cluster
```

### Part 4: Results & Business Value (5 minutes)

```bash
# View results
promptfoo view

# Show cluster status
sky status
```

**Key Benefits:**
1. **Speed**: 4 models evaluated in time of 1
2. **Flexibility**: Any model source works
3. **Cost Control**: Spot instances, auto-cleanup
4. **Enterprise Ready**: Private models, K8s support

## Quick Commands Reference

```bash
# Fine-tune a model
sky launch finetuning/finetune-to-volume.yaml -c my-finetune

# Run evaluation
python evaluate_models.py

# Check status
sky status

# View logs
sky logs eval-<model-name>

# Cleanup
sky down -a -y
```

## Key Demo Points

1. **Model Source Flexibility**
   - Public models (HuggingFace)
   - Private models (S3 buckets)
   - Persistent storage (Kubernetes volumes)

2. **Parallel Efficiency**
   - 3 models evaluated simultaneously
   - Same cost, 3x faster results

3. **Simple Integration**
   - Just specify source in YAML
   - SkyPilot handles all mounting/authentication
   - OpenAI-compatible APIs

## Troubleshooting

If something goes wrong:

```bash
# Debug specific cluster
sky logs eval-<model-name> --tail 100

# Force restart a model
sky down eval-<model-name>
sky launch templates/serve-model.yaml --cluster eval-<model-name>

# Check volume mount
sky exec eval-<model-name> "ls /volumes/"
```

## Post-Demo Follow-up

Suggest next steps:
1. Fine-tune on their proprietary data
2. Add more models to comparison
3. Integrate with their ML pipeline
4. Set up automated evaluation CI/CD
