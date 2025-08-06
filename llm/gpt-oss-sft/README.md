# Run and Serve OpenAI gpt-oss Models with SkyPilot and vLLM

![](https://i.imgur.com/TkoqCQK.png)

On August 5, 2025, OpenAI released [gpt-oss](https://openai.com/open-models/), including two state-of-the-art open-weight language models: `gpt-oss-120b` and `gpt-oss-20b`. These models deliver strong real-world performance at low cost and are available under the flexible Apache 2.0 license.

The `gpt-oss-120b` model achieves near-parity with OpenAI o4-mini on core reasoning benchmarks, while the `gpt-oss-20b` model delivers similar results to OpenAI o3-mini.

This guide walks through how to finetune both models with LoRA/full finetuning using [🤗 Accelerate](https://github.com/huggingface/accelerate).

![Cloud Logos](https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-dark.png)

## Step 0: Setup infrastructure

SkyPilot is a framework for running AI and batch workloads on any infrastructure, offering unified execution, high cost savings, and high GPU availability.

### Install SkyPilot

```bash
pip install 'skypilot[all]'
```
For more details on how to setup your cloud credentials see [SkyPilot docs](https://docs.skypilot.co).

### Choose your infrastructure

```bash
sky check
```

## Step 1: Run gpt-oss models

### Full finetuning

**For `gpt-oss-20b` (smaller model):**
- Requirements: 1 node, 8x H100 GPUs
```bash
sky launch -c gpt-oss-20b-sft gpt-oss-20b-sft.yaml
```

**For `gpt-oss-120b` (larger model):**
- Requirements: 2 nodes, 8x H100 GPUs each
```bash
sky launch -c gpt-oss-120b-sft gpt-oss-120b-sft.yaml
```

### LoRA finetuning

**For `gpt-oss-20b` with LoRA:**
- Requirements: 1 node, 2x H100 GPU
```bash
sky launch -c gpt-oss-20b-lora gpt-oss-20b-lora.yaml
```

**For `gpt-oss-120b` with LoRA:**
- Requirements: 1 node, 8x H100 GPUs
```bash
sky launch -c gpt-oss-120b-lora gpt-oss-120b-lora.yaml
```

## Step 2: Monitor and get results

Once your finetuning job is running, you can monitor the progress and retrieve results:

```bash
# Check job status
sky status

# View logs
sky logs <cluster-name>

# Download results when complete
sky down <cluster-name>
```

### Example training progress

Here's what you can expect to see during training - the loss should decrease and token accuracy should improve over time:

```
Training Progress for gpt-oss-120b:
  6%|▋         | 1/16 [01:18<19:31, 78.12s/it]
{'loss': 2.2344, 'grad_norm': 17.139, 'learning_rate': 0.0, 'num_tokens': 51486.0, 'mean_token_accuracy': 0.5436, 'epoch': 0.06}

 12%|█▎        | 2/16 [01:23<08:10, 35.06s/it]
{'loss': 2.1689, 'grad_norm': 16.724, 'learning_rate': 0.0002, 'num_tokens': 105023.0, 'mean_token_accuracy': 0.5596, 'epoch': 0.12}

 25%|██▌       | 4/16 [01:34<03:03, 15.26s/it]
{'loss': 2.1548, 'grad_norm': 3.983, 'learning_rate': 0.000192, 'num_tokens': 214557.0, 'mean_token_accuracy': 0.5182, 'epoch': 0.25}

 50%|█████     | 8/16 [01:56<00:59,  7.43s/it]
{'loss': 2.1323, 'grad_norm': 3.460, 'learning_rate': 0.000138, 'num_tokens': 428975.0, 'mean_token_accuracy': 0.5432, 'epoch': 0.5}

 75%|███████▌  | 12/16 [02:15<00:21,  5.50s/it]
{'loss': 1.4624, 'grad_norm': 0.888, 'learning_rate': 6.5e-05, 'num_tokens': 641021.0, 'mean_token_accuracy': 0.6522, 'epoch': 0.75}

100%|██████████| 16/16 [02:34<00:00,  4.88s/it]
{'loss': 1.1294, 'grad_norm': 0.713, 'learning_rate': 2.2e-05, 'num_tokens': 852192.0, 'mean_token_accuracy': 0.7088, 'epoch': 1.0}

Final Training Summary:
{'train_runtime': 298.36s, 'train_samples_per_second': 3.352, 'train_steps_per_second': 0.054, 'train_loss': 2.086, 'epoch': 1.0}
✓ Job finished (status: SUCCEEDED).
```

**Key Metrics to Monitor:**
- **Loss**: Should decrease from ~2.23 to ~1.13 (indicating learning progress)
- **Token Accuracy**: Should improve from ~54% to ~71% 
- **Gradient Norm**: Should stabilize as training progresses
- **Learning Rate**: Follows cosine schedule, decreasing over time

**Expected Performance:**
- Training time: ~5 minutes for 1 epoch on 2x8 H100 nodes
- Final loss: ~1.13 (indicates good convergence)

## Configuration files

You can find the complete configurations in [the following directory](https://github.com/skypilot-org/skypilot/blob/master/llm/gpt-oss-sft/).
