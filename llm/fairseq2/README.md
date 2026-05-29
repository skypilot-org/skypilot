# Fairseq2: Meta FAIR's Sequence Modeling Toolkit

[Fairseq2](https://github.com/facebookresearch/fairseq2) is Meta FAIR's next-generation sequence modeling toolkit. It provides recipes for LLM instruction finetuning and preference optimization, with multi-GPU and multi-node support via DDP, FSDP, and tensor parallelism.

## Why SkyPilot + Fairseq2?

SkyPilot makes fine-tuning with fairseq2 **effortless**:
- **Run anywhere** - Same YAML works on Kubernetes, Slurm, AWS, GCP, Azure, and 20+ other clouds
- **Multi-node with zero setup** - Handles distributed fine-tuning across nodes automatically
- **No vendor lock-in** - Checkpoints saved to your own cloud storage

## Quick Start

First, set up your Hugging Face token (see [Preparation](#preparation) section for details):
```bash
export HF_TOKEN=your_hf_token_here
```

Launch instruction fine-tuning on a single GPU:
```bash
cd llm/fairseq2
sky launch -c fairseq2-sft sft.sky.yaml --secret HF_TOKEN
```

Monitor training progress:
```bash
sky logs fairseq2-sft
```

## Examples

### Instruction Fine-tuning (SFT)

Fine-tune Llama 3.2 1B on the GSM8K math reasoning dataset:

```bash
sky launch -c fairseq2-sft sft.sky.yaml --secret HF_TOKEN
```

Use a larger model:
```bash
sky launch -c fairseq2-sft sft.sky.yaml --secret HF_TOKEN \
  --gpus H100:8 --env MODEL=llama3_1_70b
```

### Multi-Node Fine-tuning

Fine-tune across multiple nodes with FSDP:

```bash
sky launch -c fairseq2-multi multinode.sky.yaml --secret HF_TOKEN

# Use a larger model:
sky launch -c fairseq2-multi multinode.sky.yaml --secret HF_TOKEN \
  --gpus H100:8 --env MODEL=llama3_1_70b
```

## Preparation

### 1. Request Model Access

Llama models are gated and require access approval:
- Request access to [Llama models on Hugging Face](https://huggingface.co/meta-llama)

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
uv pip install "skypilot-nightly[aws,gcp,kubernetes,slurm]"
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
