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

Fine-tune a LLaMA model on instruction-following data:

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

Run Direct Preference Optimization training:

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

Fairseq2 supports various LLaMA model configurations:
- `llama3_2_1b` - LLaMA 3.2 1B parameter model
- `llama3_2_3b` - LLaMA 3.2 3B parameter model
- `llama3_1_8b` - LLaMA 3.1 8B parameter model
- `llama3_1_70b` - LLaMA 3.1 70B parameter model

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
- Follow the model-specific requirements

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
pip install skypilot-nightly[aws,gcp,kubernetes]
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
