# Large-scale parallel evaluation of models and agents with SkyPilot and Promptfoo

Compare multiple trained models and agents side-by-side using Promptfoo and SkyPilot.

![](https://i.imgur.com/BskVWdn.png)

![](https://i.imgur.com/ptuYADo.png)

## Architecture Overview

```
                 configs/eval_config.yaml
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
   ┌──────────┐       ┌─────────┐       ┌─────────┐
   │ Cluster  │       │ Cluster │       │ Cluster │
   │ • L4 GPU │       │ • L4 GPU│       │ • L4 GPU│
   │ • vLLM   │       │ • vLLM  │       │ • Ollama│
   └────┬─────┘       └────┬────┘       └────┬────┘
        │                  │                 │
        └──────────────────┴─────────────────┘
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
   - Handles cloud and reserved resources and GPU allocation
   - Sets up networking and exposes endpoints automatically

2. **SkyPilot Clusters**: Each model runs on its own cluster
   - Automatic GPU provisioning based on model size
   - Multiple inference engines supported (vLLM, Ollama)
   - OpenAI-compatible API endpoints
   - Auto-configured networking with public IPs

3. **Flexible Model Sources**: 
   - HuggingFace Hub for public models
   - Cloud buckets (S3/GCS) for custom checkpoints
   - SkyPilot Volumes for fast repeated access

4. **Promptfoo**: Evaluates the models and agents
   - Uses OpenAI-compatible APIs to evaluate the models
   - Saves the results to a JSON file
   - Provides a web interface to view the results

### Why SkyPilot?

**SkyPilot automates the complex infrastructure setup** needed for large-scale model evaluation:
- **Parallel deployment**: Launch 10+ models simultaneously in minutes, not hours
- **Automatic networking**: Each model gets a public endpoint with zero configuration
- **Cross-cloud orchestration**: Mix models across AWS, GCP, Azure, and Kubernetes


## Quick Start

```bash
# 1. Install dependencies
pip install skypilot[all] pyyaml
npm install -g promptfoo

# 2. Configure models and tests (edit configs/eval_config.yaml)
# 3. Run evaluation
python evaluate_models.py
```

## Project Structure

```
parallel-model-eval/
├── evaluate_models.py      # Main script
├── utils.py                # Utility functions
├── configs/
│   ├── eval_config.yaml    # Models & evaluation config
│   └── templates/          # Inference engine templates
│       ├── serve-vllm.yaml    # vLLM (default)
│       └── serve-ollama.yaml  # Ollama
├── model_stores/           # Demo model setup
│   ├── setup-volume.yaml   # Setup models in K8s volume
│   ├── setup-s3-model.yaml # Setup models in S3
│   └── README.md
├── requirements.txt        # Python dependencies
└── README.md
```

## Configuration

Edit `configs/eval_config.yaml` to specify your models and evaluation tests:

```yaml
# Models to deploy (SkyPilot will create OpenAI-compatible endpoints)
models:
  - name: "mistral-7b"
    source: "hf://mistralai/Mistral-7B-Instruct-v0.3"
    
  - name: "tinyllama"
    source: "ollama://tinyllama:1.1b"  # Use Ollama's pre-built model
    serve_template: "configs/templates/serve-ollama.yaml"
    
  - name: "agent-qwen"
    source: "s3://my-models/qwen-7b-agent"
    
  - name: "agent-llama"
    source: "volume://model-checkpoints/agent-llama"

# Deployment settings
cluster_prefix: "eval"
cleanup_on_complete: true
# default_serve_template: "configs/templates/serve-vllm.yaml"  # Optional: change default engine

# Promptfoo evaluation config (https://www.promptfoo.dev/docs/configuration/guide/)
promptfoo:
  description: "Multi-model evaluation"
  
  prompts:
    - "You are a helpful AI assistant. {{message}}"
  
  tests:
    - vars:
        message: "What is quantum computing?"
      assert:
        - type: contains
          value: "quantum"
    
    - vars:
        message: "Write a hello world in Python"
      assert:
        - type: contains
          value: "print"
        - type: python
          value: "len(output) > 10"
```

## Inference Engine Templates

The evaluation system supports multiple inference engines through configurable templates:

### Available Templates

1. **vLLM** (`configs/templates/serve-vllm.yaml`) - Default
   - High-performance GPU inference
   - Best for production deployments
   - Industry standard for LLM serving
   
2. **Ollama** (`configs/templates/serve-ollama.yaml`)
   - Simple deployment and management
   - Supports GGUF format models
   - Good for development and testing

### Using Different Engines

```yaml
# Global default for all models
default_serve_template: "configs/templates/serve-ollama.yaml"

# Per-model override
models:
  - name: "fast-model"
    source: "hf://mistralai/Mistral-7B-Instruct-v0.3"
    serve_template: "configs/templates/serve-vllm.yaml"  # Use vLLM for this model
```

## How It Works

1. **Configure**: Edit `configs/eval_config.yaml` with your models and tests
2. **Launch with SkyPilot**: The script uses SkyPilot SDK to deploy each model on its own GPU cluster
3. **Evaluate with Promptfoo**: All models receive the same test prompts
4. **Compare Results**: View outputs side-by-side in the Promptfoo UI

## Model Sources

- **HuggingFace**: `hf://org/model` - Any public model from the Hub
- **Ollama**: `ollama://model:tag` - Pre-built Ollama models
- **S3/GCS**: `s3://bucket/path` or `gs://bucket/path` - Your trained models in cloud storage
- **Volumes**: `volume://volume-name/path` - Models stored in SkyPilot volumes for fast loading

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
- `"A100-80GB:1"`/`"H100:1"` - For 70B+ models

## Example Output

```
🎯 Multi-Model Evaluation with Parallel Launch
=============================================

📛 Using cluster prefix: 'eval'
📄 Using custom default serve template: configs/templates/serve-ollama.yaml

============================================================
🚀 LAUNCHING 3 MODELS IN PARALLEL
============================================================

[1/3] Launching mistral-7b...
[2/3] Launching agent-qwen...
[3/3] Launching agent-llama...

------------------------------------------------------------
⏳ WAITING FOR CLUSTERS TO PROVISION
------------------------------------------------------------

  ✅ mistral-7b: cluster provisioned
  ✅ agent-qwen: cluster provisioned
  ✅ agent-llama: cluster provisioned

------------------------------------------------------------
🔄 WAITING FOR MODEL SERVERS TO START
------------------------------------------------------------

  ✅ mistral-7b is ready!
  🌐 mistral-7b: endpoint verified at http://34.125.23.45:8000
  ✅ agent-qwen is ready!
  🌐 agent-qwen: endpoint verified at http://35.223.12.89:8000
  ✅ agent-llama is ready!
  🌐 agent-llama: endpoint verified at http://35.198.76.12:8000

🎉 Successfully launched 3/3 models

📝 Created evaluation config for 3 models with 2 tests

🔍 Running evaluation...
✅ Evaluation complete!

View results: promptfoo view
```

## Tips

- Models run on port 8000 (no configuration needed)
- Launch happens in parallel for speed
- Results saved to `results.json`
- View detailed comparison with `promptfoo view`
- Use `eval_only: true` to run evaluation on existing clusters
- Set `cleanup_on_complete: false` to keep clusters running

## Customizing Evaluation Tests

The evaluation uses [Promptfoo's native configuration format](https://www.promptfoo.dev/docs/configuration/guide/). Add more tests to `configs/eval_config.yaml`:

```yaml
promptfoo:
  tests:
    # Test reasoning with LLM grading
    - vars:
        message: "If all roses are flowers and some flowers fade quickly, can we conclude that some roses fade quickly?"
      assert:
        - type: llm-rubric
          value: "The answer correctly identifies that we cannot make this conclusion"
    
    # Test code generation with multiple checks
    - vars:
        message: "Write a Python function to calculate factorial"
      assert:
        - type: contains
          value: "def"
        - type: python
          value: "'factorial' in output"
        - type: javascript
          value: "output.includes('return') || output.includes('yield')"
    
    # Test structured output
    - vars:
        message: "Generate a JSON object with name and age fields"
      assert:
        - type: is-json
        - type: javascript
          value: "JSON.parse(output).name && JSON.parse(output).age"
    
    # Test with similarity matching
    - vars:
        message: "Explain photosynthesis"
      assert:
        - type: similar
          value: "Photosynthesis is the process by which plants convert sunlight into chemical energy"
          threshold: 0.8
```

See [Promptfoo's assertion documentation](https://www.promptfoo.dev/docs/configuration/expected-outputs/) for all available test types.

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

To use your fine-tuned models, simply save them to a volume or S3 bucket during training, then reference in `configs/eval_config.yaml`.

## Troubleshooting

```bash
# View model logs
sky logs eval-<model-name>

# List running clusters
sky status

# Check cluster endpoints
sky endpoints eval-<model-name> 8000

# Manually cleanup
sky down eval-*

# Use existing clusters without relaunching
# Set eval_only: true in configs/eval_config.yaml
```

## Advanced Usage

### Custom Serving Templates

Create your own serving template by copying and modifying an existing one:

```yaml
# configs/templates/serve-custom.yaml
envs:
  MODEL_PATH: model-default
  API_TOKEN: default-token

resources:
  accelerators: L4:1
  ports: 8000

setup: |
  # Your setup commands

run: |
  # Start server with OpenAI-compatible API on port 8000
  # Use $MODEL_PATH and $API_TOKEN
```

Requirements for custom templates:
- Must expose OpenAI-compatible API on port 8000
- Should use `$MODEL_PATH` and `$API_TOKEN` environment variables
- Should handle HuggingFace, S3, and volume model sources
