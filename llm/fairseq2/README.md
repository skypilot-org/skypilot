# Fairseq2: Meta FAIR's Sequence Modeling Toolkit

[Fairseq2](https://github.com/facebookresearch/fairseq2) is Meta AI Research's next-generation sequence modeling toolkit. It provides a clean, modular API for training and fine-tuning large language models with support for instruction fine-tuning, preference optimization (DPO, CPO, SimPO, ORPO), and scalable distributed training.

## Why SkyPilot + Fairseq2?

SkyPilot makes training with fairseq2 **effortless**:
- **Run anywhere** - Same YAML works on Kubernetes, AWS, GCP, Azure, and 20+ other clouds
- **3x cheaper** with managed spot instances and automatic recovery
- **Multi-node with zero setup** - Handles distributed training across nodes automatically
- **No vendor lock-in** - Checkpoints saved to your own cloud storage

## Quick Start

First, set up your Hugging Face token (see [Preparation](#preparation) section for details):
```bash
export HF_TOKEN=your_hf_token_here
```

Launch instruction fine-tuning on a single GPU:
```bash
sky launch -c fairseq2 llm/fairseq2/sft.yaml --secret HF_TOKEN
```

Monitor training progress:
```bash
sky logs fairseq2
```

## Examples

### Instruction Fine-tuning (SFT)

Fine-tune a LLaMA model on the GSM8K math reasoning dataset:

```bash
sky launch -c fairseq2-sft llm/fairseq2/sft.yaml --secret HF_TOKEN
```

Scale to 8 GPUs:
```bash
sky launch -c fairseq2-sft llm/fairseq2/sft.yaml --secret HF_TOKEN --gpus A100:8
```

Use a larger model:
```bash
sky launch -c fairseq2-sft llm/fairseq2/sft.yaml --secret HF_TOKEN --env MODEL=llama3_1_8b
```

### Preference Optimization (DPO)

Run Direct Preference Optimization training on the GSM8K preference dataset:

```bash
sky launch -c fairseq2-dpo llm/fairseq2/dpo.yaml --secret HF_TOKEN
```

### Multi-Node Distributed Training

Train larger models across multiple nodes with FSDP:

```bash
# 2 nodes with 8 H100s each (16 GPUs total)
sky launch -c fairseq2-multi llm/fairseq2/multinode.yaml --secret HF_TOKEN

# Scale to 4 nodes (32 GPUs total)
sky launch -c fairseq2-multi llm/fairseq2/multinode.yaml --secret HF_TOKEN --num-nodes 4
```

## Configuration Options

### Supported Models

Fairseq2 supports multiple model families for language modeling tasks:

| Model Family | Example Configs | Description |
|--------------|-----------------|-------------|
| **LLaMA** | `llama3_2_1b`, `llama3_2_3b`, `llama3_1_8b`, `llama3_1_70b` | Meta's LLaMA models (gated) |
| **LLaMA 4** | `llama4_*` | Latest LLaMA 4 variants (gated) |
| **Mistral** | `mistral_7b` | Mistral AI's models |
| **Qwen 2.5** | `qwen25_1_5b`, `qwen25_3b`, `qwen25_7b`, `qwen25_14b`, `qwen25_32b` | Alibaba's Qwen 2.5 models |
| **Qwen 3** | `qwen3_0.6b`, `qwen3_1.7b`, `qwen3_4b`, `qwen3_8b`, `qwen3_14b`, `qwen3_32b` | Alibaba's Qwen 3 models (non-gated) |

Fairseq2 also supports speech and vision models (Wav2Vec2, Conformer, ViT, NLLB) for multimodal tasks.

### Dataset

These examples use the [facebook/fairseq2-lm-gsm8k](https://huggingface.co/datasets/facebook/fairseq2-lm-gsm8k) dataset, which contains:

| Split | Description |
|-------|-------------|
| `sft_train/` | SFT training data (instruction-response pairs) |
| `sft_test/` | SFT test data for evaluation |
| `dpo_train/` | DPO preference pairs (chosen vs rejected) |
| `dpo_test/` | DPO test data |

The dataset is automatically downloaded during setup.

### Training Parameters

Customize training by setting environment variables:

```bash
sky launch -c fairseq2 llm/fairseq2/sft.yaml --secret HF_TOKEN \
  --env MODEL=llama3_1_8b \
  --env MAX_NUM_STEPS=2000 \
  --env MAX_NUM_TOKENS=8192
```

## Preparation

### 1. Request Model Access

Some models require access approval:
- Request access to [LLaMA models on Hugging Face](https://huggingface.co/meta-llama)
- Qwen models are non-gated and don't require approval

### 2. Get Your Hugging Face Token

1. Go to [Hugging Face Settings > Tokens](https://huggingface.co/settings/tokens)
2. Create a new token with "Read" permissions
3. Copy the token for the next step

### 3. Set Environment Variable

```bash
export HF_TOKEN="your_token_here"
```

### 4. Install SkyPilot

```bash
uv pip install "skypilot-nightly[aws,gcp,kubernetes]"
# See: https://docs.skypilot.co/en/latest/getting-started/installation.html
```

### 5. Verify Setup

```bash
sky check
```

## Learn More

- [Fairseq2 GitHub Repository](https://github.com/facebookresearch/fairseq2)
- [Fairseq2 Documentation](https://facebookresearch.github.io/fairseq2/stable/)
- [Fairseq2 Quick Start Wiki](https://github.com/facebookresearch/fairseq2/wiki/Quick-Start)
- [SkyPilot Documentation](https://docs.skypilot.co/)
- [DPO Paper](https://arxiv.org/abs/2305.18290)
