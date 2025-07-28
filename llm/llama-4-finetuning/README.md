# Finetune Llama 4 on your infra

<figure>
<center>
<img src="https://i.imgur.com/KORygbI.png" width="90%">
</figure>

Meta's Llama 4 represents the next generation of open-source large language models, featuring advanced capabilities with the **Llama-4-Maverick-17B-128E model** - a 400B  parameter (17B active) Mixture of Experts (MoE) architecture with 128 experts.

This guide shows how to use [SkyPilot](https://github.com/skypilot-org/skypilot) with torchtune and Llama Factory to **finetune Llama 4 on your own infra**. Everything is packaged in simple [SkyPilot YAMLs](https://docs.skypilot.co/en/latest/getting-started/quickstart.html), that can be launched with one command on your infra: 
- Kubernetes cluster
- Cloud accounts ([16+ clouds supported](https://docs.skypilot.co/en/latest/getting-started/installation.html))

## 📁 Available Recipes

Choose the right recipe for your needs:

| **Recipe** | **Requirements** | **Description** |
|------------------------|------------------|-----------------|
| 🌟 **llama-4-maverick-sft.yaml** | **4 nodes**<br>32x H200 GPUs<br>1000+ GB CPU memory per node | Full finetuning using torchtune with CPU offloading with 400B model. Recommended if you have 32 or more H200s. |
| 🎯 **llama-4-maverick-lora.yaml** | **2 nodes**<br>16x H100 GPUs<br>1000+ GB CPU memory per node | **Memory efficient** - LoRA fine-tuning with lower resource requirements. Great for limited GPU resources. |
| 🚀 **llama-4-scout-sft.yaml** | **2 nodes**<br>16x H100 GPUs<br>1000+ GB CPU memory per node | Full finetuning using torchtune with 109B model. Good start for users with H100s. |

## Full finetuning with CPU offloading

This approach uses [torchtune](https://github.com/pytorch/torchtune) to do full supervised fine-tuning with CPU offloading to reduce GPU memory requirements. Requires 32 or more H200s.

**SkyPilot YAML**: [`llama-4-maverick-sft.yaml`](https://github.com/skypilot-org/skypilot/blob/master/llm/llama-4-finetuning/llama-4-maverick-sft.yaml)

Run the following on your local machine:

<details>
    <summary>
        SkyPilot YAML for finetuning Llama 4: <code>llama-4-maverick-sft.yaml</code>
    </summary>
    
```yaml
# Full finetuning of Llama-4 Maverick 17B MoE model with 128 experts.
#
# Usage:
#
#  HF_TOKEN=xxx sky launch llama-4-maverick-sft.yaml -c maverick --env HF_TOKEN
#
# This config requires at least 4 nodes with 8x H200 GPUs each.

envs:
  HF_TOKEN: 

resources:
  cpus: 100+
  memory: 1000+
  accelerators: H200:8
  disk_tier: best

num_nodes: 4

# Optional: configure buckets for dataset and checkpoints. You can then use the /outputs directory to write checkpoints.
# file_mounts:
#  /dataset:
#    source: s3://my-dataset-bucket
#    mode: COPY  # COPY mode will prefetch the dataset to the node for faster access
#  /checkpoints:
#    source: s3://my-checkpoint-bucket
#    mode: MOUNT_CACHED  # MOUNT_CACHED mode will intelligently cache the checkpoint for faster writes

setup: |
  # Install torch and torchtune nightly builds
  pip install --pre --upgrade torch==2.8.0.dev20250610+cu126 torchvision==0.23.0.dev20250610+cu126 torchao==0.12.0.dev20250611+cu126 --index-url https://download.pytorch.org/whl/nightly/cu126 # full options are cpu/cu118/cu124/cu126/xpu/rocm6.2/rocm6.3/rocm6.4
  pip install --pre --upgrade torchtune==0.7.0.dev20250610+cpu --extra-index-url https://download.pytorch.org/whl/nightly/cpu

  # Download the model (~700 GB, may take time to download)
  tune download meta-llama/Llama-4-Maverick-17B-128E-Instruct \
    --hf-token $HF_TOKEN

run: |
  MASTER_ADDR=$(echo "$SKYPILOT_NODE_IPS" | head -n1)
  echo "Starting distributed finetuning, head node: $MASTER_ADDR"

  tune run \
  --nnodes $SKYPILOT_NUM_NODES \
  --nproc_per_node $SKYPILOT_NUM_GPUS_PER_NODE \
  --rdzv_id $SKYPILOT_TASK_ID \
  --rdzv_backend c10d \
  --rdzv_endpoint=$MASTER_ADDR:29500 \
  full_finetune_distributed \
  --config llama4/maverick_17B_128E_full \
  model_dir=/tmp/Llama-4-Maverick-17B-128E-Instruct \
  dataset.packed=True tokenizer.max_seq_len=4096 \
  gradient_accumulation_steps=1 \
  enable_activation_offloading=True \
  activation_offloading_use_streams=False \
  optimizer_in_bwd=True \
  optimizer=torch.optim.AdamW \
  optimizer_kwargs.fused=True \
  max_steps_per_epoch=1 \
  epochs=10 \
  enable_dcp=True \
  enable_async_checkpointing=True \
  resume_from_checkpoint=False \
  keep_last_n_checkpoints=1 \
  fsdp_cpu_offload=True
```
    
</details>

Run the following on your local machine:

```bash
# Download the files for Llama 4 finetuning
git clone https://github.com/skypilot-org/skypilot
cd skypilot/llm/llama-4-finetuning

export HF_TOKEN=xxxx
sky launch -c maverick-torchtune llama-4-maverick-sft.yaml \
  --env HF_TOKEN
```

## Alternative Approaches

### LoRA Fine-tuning (Lower Resource Requirements)
For users with limited GPU resources, LoRA (Low-Rank Adaptation) provides an efficient alternative that can run on 16 H100s:

```bash
# LoRA finetuning - requires fewer resources
sky launch -c maverick-lora llama-4-maverick-lora.yaml \
  --env HF_TOKEN
```

## Appendix: Preparation
1. Request the access to [Llama 4 weights on huggingface](https://huggingface.co/meta-llama/Llama-4-Maverick-17B-128E-Instruct) (Click on the blue box and follow the steps).

2. Get your [huggingface access token](https://huggingface.co/settings/tokens):
![](https://i.imgur.com/3idBgHn.png)


3. Add huggingface token to your environment variable:
```bash
export HF_TOKEN="xxxx"
```

4. Install SkyPilot for launching the finetuning:
```bash
pip install skypilot-nightly[aws,gcp,kubernetes] 
# or other clouds (16 clouds + kubernetes supported) you have setup
# See: https://docs.skypilot.co/en/latest/getting-started/installation.html
```

5. Check your infra setup:
```console
sky check

🎉 Enabled clouds 🎉
    ✔ AWS
    ✔ GCP
    ✔ Azure
    ✔ OCI
    ✔ Lambda
    ✔ RunPod
    ✔ Paperspace
    ✔ Fluidstack
    ✔ Cudo
    ✔ IBM
    ✔ SCP
    ✔ vSphere
    ✔ Cloudflare (for R2 object store)
    ✔ Kubernetes
```

## What's next
    
* [AI on Kubernetes Without the Pain](https://blog.skypilot.co/ai-on-kubernetes/)
* [SkyPilot AI Gallery](https://docs.skypilot.co/en/latest/gallery/index.html)
* [SkyPilot Docs](https://docs.skypilot.co)
* [SkyPilot GitHub](https://github.com/skypilot-org/skypilot)
