# Large-scale Parallel Model Evaluation with SkyPilot and Promptfoo

**Compare multiple LLMs side-by-side in minutes** - Launch dozens of models in parallel across any infrastructure (clouds, Kubernetes, etc.) and evaluate them with the same test suite.

## Model Comparison Dashboard
![Model Comparison Dashboard](https://i.imgur.com/IuKgPTV.png)

## Self-hosted models by SkyPilot
![Self-hosted models by SkyPilot](https://i.imgur.com/ptuYADo.png)

## 🚀 What This Does

This tool lets you:
- **Launch multiple LLMs in parallel** across any infrastructure (AWS, GCP, Azure, Kubernetes)
- **Run the same evaluation tests** on all models to compare quality
- **See results side-by-side** in an interactive dashboard
- **Use any model source**: HuggingFace, Ollama, your custom models in S3/GCS, or Kubernetes PVC.

### Why Use This?

✅ **Speed**: Launch 10+ models in parallel in under 5 minutes  
✅ **Simplicity**: One command to deploy and evaluate everything  
✅ **Flexibility**: Mix models from different sources and infrastructure  
✅ **Cost-effective**: Automatic cleanup after evaluation completes  

## 🎯 Quick Start (5 minutes)

### 1️⃣ Install Dependencies

```bash
# Install SkyPilot and evaluation tools
# Choose your infrastructure. Alternatively, you can install other clouds with
# `uv pip install "skypilot[aws,gcp,kubernetes]"`.
uv pip install "skypilot[kubernetes]"
npm install -g promptfoo

# Verify your infrastructure is accessible
sky check
```

### 2️⃣ Run Your First Evaluation

```bash
# Run the default evaluation with 3 models
python evaluate_models.py

# View results in your browser
promptfoo view
```

That's it! You've just compared multiple models in parallel. You can find the comparison dashboard [above](#model-comparison-dashboard).

### 3️⃣ Customize for Your Use Case

Edit `configs/eval_config.yaml` to:
- Add your own models
- Customize evaluation tests
- Change GPU types
- Select different infrastructure

### 4️⃣ Use your own models

You can use your own models by adding them to the `models` section of `configs/eval_config.yaml`.
The model checkpoints can be stored in S3, GCS, or Kubernetes volumes. You can find examples in the [model_stores/](model_stores/) directory.

#### Storing your model checkpoints examples:

* Store your model checkpoints to cloud buckets, e.g. S3 or GCS:
  ```
  sky launch -c setup-s3 model_stores/setup-s3-model.yaml
  ```

* Store your model checkpoints to Kubernetes volumes:
  * Create a SkyPilot volume for storing your model checkpoints:
    ```
    sky volumes apply model_stores/create-volume.yaml
    ```
  * Launch a cluster with the volume:
    ```
    sky launch -c setup-volume model_stores/setup-volume.yaml
    ```

See [model_stores](model_stores/) for more details.

#### Evaluating your models

To use S3 bucket or volume as model source in `configs/eval_config.yaml`, use the following format:
```yaml
models:
  - name: "my-model"
    source: "s3://my-bucket/models/my-model"
```
or
```yaml
models:
  - name: "my-model"
    source: "volume://my-volume/models/my-model"
```

## 📁 What's in This Directory?

```
parallel-model-eval/
├── evaluate_models.py      # ← Run this to start evaluation
├── configs/
│   ├── eval_config.yaml    # ← Edit this to configure models & tests
│   └── templates/          # Inference engine configs (vLLM, Ollama)
└── model_stores/           # Examples for self-hosting models for evaluation
```

## ⚙️ Configuration Guide

### Basic Configuration

The entire evaluation is configured in one file: `configs/eval_config.yaml`

```yaml
# 1. MODELS TO COMPARE
models:
  # Example: Public model from HuggingFace
  - name: "mistral-7b"
    source: "hf://mistralai/Mistral-7B-Instruct-v0.1"
    accelerators: "L4:1"  # Optional: specify GPU type
    
  # Example: Your fine-tuned model in S3
  - name: "my-custom-model"
    source: "s3://my-bucket/models/fine-tuned-llama"
    
  # Example: Small model with Ollama for quick testing
  - name: "tinyllama"
    source: "ollama://tinyllama:1.1b"
    serve_template: "configs/templates/serve-ollama.yaml"

# 2. EVALUATION SETTINGS
cluster_prefix: "eval"          # Prefix for cluster names
cleanup_on_complete: true        # Auto-cleanup after evaluation
eval_only: false                 # Set to true to run evaluation on existing clusters

# 3. TEST SUITE
promptfoo:
  description: "Comparing model capabilities"
  
  prompts:
    - "You are a helpful assistant. {{message}}"
  
  tests:
    # Test 1: Basic QA
    - vars:
        message: "What is 2+2?"
      assert:
        - type: contains
          value: "4"
    
    # Test 2: Code generation
    - vars:
        message: "Write a Python function to reverse a string"
      assert:
        - type: contains
          value: "def"
        - type: python
          value: "'return' in output or 'yield' in output"
```

## 🛠️ Common Scenarios

### Compare Open-Source Models
```yaml
models:
  - name: "llama-3-8b"
    source: "hf://meta-llama/Meta-Llama-3-8B-Instruct"
  - name: "mistral-7b"
    source: "hf://mistralai/Mistral-7B-Instruct-v0.3"
  - name: "qwen-7b"
    source: "hf://Qwen/Qwen2-7B-Instruct"
```

### Compare Your Fine-Tuned Models
```yaml
models:
  - name: "baseline"
    source: "s3://my-models/baseline-llama"
  - name: "fine-tuned-v1"
    source: "s3://my-models/finetuned-v1"
  - name: "fine-tuned-v2"
    source: "s3://my-models/finetuned-v2"
```

### Mix Different Model Sizes
```yaml
models:
  - name: "small-fast"
    source: "hf://TinyLlama/TinyLlama-1.1B-Chat-v1.0"
    accelerators: "T4:1"  # Cheaper GPU
  - name: "medium-balanced"
    source: "hf://mistralai/Mistral-7B-Instruct-v0.3"
    accelerators: "L4:1"
  - name: "large-powerful"
    source: "hf://meta-llama/Meta-Llama-3-70B-Instruct"
    accelerators: "A100-80GB:1"  # Powerful GPU
```

## 🔧 How It Works

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


1. **You define** models and tests in `configs/eval_config.yaml`
2. **SkyPilot launches** each model on its own GPU cluster in parallel and exposes OpenAI-compatible APIs
3. **Promptfoo runs** your test suite against all models and generates a report

## 📦 Supported Model Sources

| Source | Format | Example | Use Case |
|--------|--------|---------|----------|
| **HuggingFace** | `hf://org/model` | `hf://mistralai/Mistral-7B-v0.3` | Public models |
| **Ollama** | `ollama://model:tag` | `ollama://llama3:8b` | Quick testing |
| **Cloud Bucket** | `s3://bucket/path` or `gs://bucket/path` | `s3://my-models/llama-fine-tuned` | Your trained models |
| **Volume** | `volume://name/path` | `volume://checkpoints/model-v2` | Fast repeated access |

### 💾 Using Your Own Models

#### From S3/GCS
```yaml
models:
  - name: "my-finetuned-model"
    source: "s3://my-bucket/models/checkpoint-5000"
    # Model will be downloaded automatically when cluster starts
```

#### From SkyPilot Volumes (Faster for Repeated Use)
```bash
# One-time: Create a volume and upload your model
sky volumes create model-storage --size 100
sky volumes cp local-model-dir volume://model-storage/my-model

# Use in evaluation
models:
  - name: "my-model"
    source: "volume://model-storage/my-model"
```


## 🎬 What to Expect

When you run `python evaluate_models.py`:

```
🚀 LAUNCHING 3 MODELS IN PARALLEL
  [1/3] Launching mistral-7b...
  [2/3] Launching my-finetuned-model...
  [3/3] Launching llama-3-8b...

⏳ WAITING FOR CLUSTERS (2-3 minutes)
  ✅ mistral-7b ready at http://34.125.23.45:8000
  ✅ my-finetuned-model ready at http://35.223.12.89:8000
  ✅ llama-3-8b ready at http://35.198.76.12:8000

🔍 RUNNING EVALUATION
  Testing: "What is quantum computing?"
  Testing: "Write hello world in Python"
  Testing: "Explain recursion"

✅ COMPLETE! View results: promptfoo view
```

Total time: ~5 minutes for 3 models

## 💡 Pro Tips

### Avoid restarting clusters

You can set `eval_only: true` in the `configs/eval_config.yaml` to re-run tests on existing clusters.

```yaml
eval_only: true 
```

### Debug Issues
```bash
# Check cluster status
sky status

# View model logs
sky logs eval-<model-name>

# SSH into a cluster
sky ssh eval-<model-name>

# Manual cleanup if needed
sky down eval-*
```

## 📝 Writing Evaluation Tests

### Test Types You Can Use

```yaml
promptfoo:
  tests:
    # 1. EXACT MATCHING - Check for specific content
    - vars:
        message: "What is the capital of France?"
      assert:
        - type: contains
          value: "Paris"
    
    # 2. CODE VALIDATION - Verify code generation
    - vars:
        message: "Write a bubble sort function"
      assert:
        - type: python  # Run Python code to check output
          value: |
            def check(output):
                return 'def ' in output and 'for' in output
            check(output)
    
    # 3. LLM AS JUDGE - Use GPT-4 to grade responses
    - vars:
        message: "Explain quantum entanglement to a 5-year-old"
      assert:
        - type: llm-rubric
          value: "Response uses simple language appropriate for a child"
    
    # 4. FORMAT VALIDATION - Check response structure
    - vars:
        message: "Return a JSON array of 3 colors"
      assert:
        - type: is-json
        - type: javascript
          value: "Array.isArray(JSON.parse(output)) && JSON.parse(output).length === 3"
```

[Full test documentation →](https://www.promptfoo.dev/docs/configuration/expected-outputs/)

## 🚀 Real-World Example: Comparing RAG Models

```yaml
# configs/eval_config.yaml
models:
  - name: "rag-baseline"
    source: "s3://my-models/rag-v1-baseline"
  - name: "rag-with-reranking"
    source: "s3://my-models/rag-v2-reranking"
  - name: "rag-fine-tuned"
    source: "s3://my-models/rag-v3-finetuned"

promptfoo:
  tests:
    - vars:
        message: "What was our Q3 revenue?"
        context: "Q3 2024 Financial Report: Revenue: $12.5M..."
      assert:
        - type: contains
          value: "12.5"
    
    - vars:
        message: "Who is the CEO?"
        context: "Company Leadership: CEO: Jane Smith..."
      assert:
        - type: contains
          value: "Jane Smith"
```

Run evaluation:
```bash
python evaluate_models.py
# All 3 models launch in parallel
# Same tests run on each
# Compare accuracy in the dashboard
```

## ❓ FAQ

**Q: Can I use models from different clouds?**  
A: Yes! Mix models across AWS, GCP, Azure, and Kubernetes in the same evaluation.

**Q: What if a model fails to launch?**  
A: The evaluation continues with successful models. Check logs with `sky logs eval-<model-name>`. After fixing the issue, you can re-run the `python evaluate_models.py` command, which will try restarting the models with the new config.

**Q: How do I use my fine-tuned models?**  
A: Upload to S3/GCS and reference with `source: "s3://bucket/path"` or Kubernetes PVC (see how to use [SkyPilot volumes for finetuning](https://docs.skypilot.co/en/latest/reference/volumes.html#volumes-on-kubernetes)).

**Q: Can I customize the serving setup?**  
A: Yes! Create custom templates in `configs/templates/`. See Advanced Usage below.

## 🔬 Advanced Usage

### Custom Inference Engines

Use different serving frameworks:

```yaml
models:
  - name: "model-ollama"
    source: "ollama://tinyllama:1.1b"
    serve_template: "configs/templates/serve-ollama.yaml"
```

## 📚 Learn More

- [SkyPilot Documentation](https://docs.skypilot.co)
- [Promptfoo Documentation](https://www.promptfoo.dev)
- [SkyPilot Slack Community](https://slack.skypilot.co)

