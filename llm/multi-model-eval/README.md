# Large-scale parallel evaluation of models and agents with SkyPilot and Promptfoo

Compare multiple trained models and agents side-by-side using Promptfoo and SkyPilot.

## Architecture Overview

```
                 configs/models_config.yaml
                           │
                           ▼
                  python evaluate_models.py
                           │
                           ▼
                ┌──────────────────────┐
                │      SkyPilot        │
                │  Parallel Launch     │
                └──────────┬───────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
   HuggingFace       Cloud Bucket   Kubernetes Volume
   mistral-7b         agent-qwen       agent-llama
        │                  │                  │
        ▼                  ▼                  ▼
   ┌─────────┐        ┌─────────┐       ┌─────────┐
   │ Cluster │        │ Cluster │       │ Cluster │
   │ • L4 GPU│        │ • L4 GPU│       │ • L4 GPU│
   │ • vLLM  │        │ • vLLM  │       │ • vLLM  │
   └────┬────┘        └────┬────┘       └────┬────┘
        │                  │                  │
        └──────────────────┴──────────────────┘
                           │
                   OpenAI-compatible APIs
                           │
                           ▼
                   ┌──────────────┐
                   │  Promptfoo   │
                   │  Evaluation  │
                   └──────────────┘
```

### Key Components:

1. **SkyPilot SDK**: Orchestrates the entire deployment
   - Loads serving configuration from YAML template
   - Launches multiple clusters in parallel
   - Handles cloud resources and GPU allocation

2. **SkyPilot Clusters**: Each model runs on its own cluster
   - Automatic GPU provisioning based on model size
   - vLLM for high-performance inference
   - OpenAI-compatible API endpoints

3. **Flexible Model Sources**: 
   - HuggingFace Hub for public models
   - Cloud buckets (S3/GCS) for custom checkpoints
   - SkyPilot Volumes for fast repeated access

## Quick Start

```bash
# 1. Install dependencies
pip install skypilot[all] pyyaml
npm install -g promptfoo

# 2. Configure models (edit configs/models_config.yaml)
# 3. Run evaluation
python evaluate_models.py
```

## Project Structure

```
multi-model-eval/
├── evaluate_models.py      # Main script
├── configs/
│   ├── models_config.yaml  # Your model configurations
│   └── serve-model.yaml    # vLLM serving template
├── scripts/
│   └── test_setup.sh       # Check dependencies
├── model_stores/           # Demo model setup
│   ├── setup-volume.yaml   # Setup models in K8s volume
│   ├── setup-s3-model.yaml # Setup models in S3
│   └── README.md
└── README.md
```

## Model Configuration

Edit `configs/models_config.yaml` to specify your models:

```yaml
models:
  # Public model from HuggingFace
  - name: "mistral-7b"
    source: "hf://mistralai/Mistral-7B-Instruct-v0.3"
    
  # Custom model from S3 bucket
  - name: "agent-qwen"
    source: "s3://my-models/qwen-7b-agent"
    
  # Model from SkyPilot volume
  - name: "agent-llama"
    source: "volume://model-checkpoints/agent-llama"

cleanup_on_complete: true
```

## How It Works

1. **Configure Models**: Edit `configs/models_config.yaml` with your model sources
2. **Launch with SkyPilot**: The script uses SkyPilot SDK to deploy each model on its own GPU cluster
3. **Evaluate with Promptfoo**: All models receive the same test prompts
4. **Compare Results**: View outputs side-by-side in the Promptfoo UI

## Model Sources

- **HuggingFace**: Any public model from the Hub
- **S3/GCS**: Your trained models in cloud storage
- **Volumes**: Models stored in SkyPilot volumes for fast loading

### Using SkyPilot Volumes (Kubernetes)

For Kubernetes deployments, SkyPilot volumes provide persistent storage for models. See `model_stores/README.md` for detailed setup instructions and examples of using your own fine-tuned models.

**Quick setup:**
```bash
# Create volume (one-time setup)
sky volumes apply model_stores/create-volume.yaml

# Reference in model config
models:
  - name: "my-model"
    source: "volume://model-checkpoints/my-model"
    accelerators: "L4:1"
```

The path format is `volume://<volume-name>/<path-within-volume>`

### Multiple Volumes and Buckets

The evaluation tool automatically handles multiple volumes and buckets by creating unique mount points:

```yaml
models:
  # Different S3 buckets get unique mount points
  - name: "model-1"
    source: "s3://bucket-a/models/llama"  # Mounts at /buckets/bucket-a/
    
  - name: "model-2"  
    source: "s3://bucket-b/checkpoints/qwen"  # Mounts at /buckets/bucket-b/
    
  # Different volumes also get unique mount points
  - name: "model-3"
    source: "volume://volume-1/agent-model"  # Mounts at /volumes/volume-1/
    
  - name: "model-4"
    source: "volume://volume-2/base-model"  # Mounts at /volumes/volume-2/
```

This prevents conflicts when using models from multiple sources.

## GPU Selection

Common configurations:
- `"L4:1"` - Good for 7B models
- `"A10:1"` - Good for 7-13B models  
- `"A100:1"` - For larger models
- `"A100-80GB:1"` - For 70B+ models

## Example Output

```
🎯 Multi-Model Evaluation
=========================

📋 Launching 3 models...

🚀 Launching mistral-7b...
✅ Launched eval-mistral-7b
📡 Endpoint: http://34.125.23.45:8000/v1

🚀 Launching agent-qwen...
✅ Launched eval-agent-qwen
📡 Endpoint: http://35.223.12.89:8000/v1

🚀 Launching agent-deepseek...
✅ Launched eval-agent-deepseek
📡 Endpoint: http://35.198.76.12:8000/v1

✅ Successfully launched 3 models

📝 Created evaluation config for 3 models

🔍 Running evaluation...
✅ Evaluation complete!

View results: promptfoo view
```

## Tips

- Models run on port 8000 (no configuration needed)
- Launch happens in parallel for speed
- Results saved to `results.json`
- View detailed comparison with `promptfoo view`

## Model Stores

See `model_stores/` for:
- Setting up demo models in volumes and S3 buckets
- Using your own fine-tuned models with the evaluation

Quick setup:
```bash
# Setup demo models
cd model_stores
sky volumes apply create-volume.yaml  # One-time setup
sky launch setup-volume.yaml -c setup --down -y
```

To use your fine-tuned models, simply save them to a volume or S3 bucket during training, then reference in `configs/models_config.yaml`.

## Troubleshooting

```bash
# Check dependencies
./scripts/test_setup.sh

# View model logs
sky logs eval-<model-name>

# List running clusters
sky status

# Manually cleanup
sky down -a
```
