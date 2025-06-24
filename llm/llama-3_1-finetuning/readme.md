# Finetune Llama 3.1 on your infra

<figure>
<center>
<img src="https://i.imgur.com/Fr5Jq1f.png" width="90%">
</figure>

On July 23, 2024, Meta released the [Llama 3.1 model family](https://ai.meta.com/blog/meta-llama-3-1/), including a 405B parameter model in both base model and instruction-tuned forms. Llama 3.1 405B became _the first open LLM that closely rivals top proprietary models_ like GPT-4o and Claude 3.5 Sonnet.

This guide shows how to use [SkyPilot](https://github.com/skypilot-org/skypilot) and [torchtune](https://pytorch.org/torchtune/stable/index.html) to **finetune Llama 3.1 on your own data and infra**. Everything is packaged in a simple [SkyPilot YAML](https://docs.skypilot.co/en/latest/getting-started/quickstart.html), that can be launched with one command on your infra: 
- Local GPU workstation
- Kubernetes cluster
- Cloud accounts ([12 clouds supported](https://docs.skypilot.co/en/latest/getting-started/installation.html))

<figure>
<center>
<img src="https://i.imgur.com/VxMuKJn.png" width="90%">
</figure>



## Let's finetune Llama 3.1
We will use [torchtune](https://pytorch.org/torchtune/stable/index.html) to finetune Llama 3.1. The example below uses the [`yahma/alpaca-cleaned`](https://huggingface.co/datasets/yahma/alpaca-cleaned) dataset, which you can replace with your own dataset later.

To set up the environment for launching the finetuning job, finish the [Appendix: Preparation](#appendix-preparation) section first.

The finetuning job is packaged in a SkyPilot YAML. It can be launched on any of your own infra, such as Kubernetes or any cloud, with the same interface:

<details>
    <summary>
        SkyPilot YAML for finetuning Llama 3.1: <code>lora.yaml</code>
    </summary>
    
```yaml
# LoRA finetuning Meta Llama 3.1 on any of your own infra.
#
# Usage:
#
#  HF_TOKEN=xxx sky launch lora.yaml -c llama31 --secret HF_TOKEN
#
# To finetune a 70B model:  
#
#  HF_TOKEN=xxx sky launch lora.yaml -c llama31-70 --secret HF_TOKEN --env MODEL_SIZE=70B

envs:
  MODEL_SIZE: 8B
  HF_TOKEN:
  DATASET: "yahma/alpaca-cleaned"
  # Change this to your own checkpoint bucket
  CHECKPOINT_BUCKET_NAME: sky-llama-31-checkpoints


resources:
  accelerators: A100:8
  disk_tier: best
  use_spot: true

file_mounts:
  /configs: ./configs
  /output:
    name: $CHECKPOINT_BUCKET_NAME
    mode: MOUNT
    # Optionally, specify the store to enforce to use one of the stores below:
    #   r2/azure/gcs/s3/cos
    # store: r2

setup: |
  pip install torch torchvision

  # Install torch tune from source for the latest Llama 3.1 model
  pip install git+https://github.com/pytorch/torchtune.git@58255001bd0b1e3a81a6302201024e472af05379
  # pip install torchtune
  
  tune download meta-llama/Meta-Llama-3.1-${MODEL_SIZE}-Instruct \
    --hf-token $HF_TOKEN \
    --output-dir /tmp/Meta-Llama-3.1-${MODEL_SIZE}-Instruct \
    --ignore-patterns "original/consolidated*"

run: |
  tune run --nproc_per_node $SKYPILOT_NUM_GPUS_PER_NODE \
    lora_finetune_distributed \
    --config /configs/${MODEL_SIZE}-lora.yaml \
    dataset.source=$DATASET

  # Remove the checkpoint files to save space, LoRA serving only needs the
  # adapter files.
  rm /tmp/Meta-Llama-3.1-${MODEL_SIZE}-Instruct/*.pt
  rm /tmp/Meta-Llama-3.1-${MODEL_SIZE}-Instruct/*.safetensors
  
  mkdir -p /output/$MODEL_SIZE-lora
  rsync -Pavz /tmp/Meta-Llama-3.1-${MODEL_SIZE}-Instruct /output/$MODEL_SIZE-lora
  cp -r /tmp/lora_finetune_output /output/$MODEL_SIZE-lora/
```
    
</details>

Run the following on your local machine:

```bash
# Download the files for Llama 3.1 finetuning
git clone https://github.com/skypilot-org/skypilot
cd skypilot/llm/llama-3_1-finetuning

export HF_TOKEN=xxxx

# It takes about 40 mins on 8 A100 GPUs to finetune a 8B
# Llama3.1 model with LoRA on Alpaca dataset.
sky launch -c llama31 lora.yaml \
  --secret HF_TOKEN  --env MODEL_SIZE=8B \
  --env CHECKPOINT_BUCKET_NAME="your-own-bucket-name"
```


To finetune a larger model with 70B parameters, you can simply change the parameters as below:
```bash
sky launch -c llama31-70 lora.yaml \
  --secret HF_TOKEN  --env MODEL_SIZE=70B \
  --env CHECKPOINT_BUCKET_NAME="your-own-bucket-name"
```

**Finetuning Llama 3.1 405B**: Work in progress! If you want to follow the work, join the [SkyPilot community Slack](https://slack.skypilot.co/) for discussions.

## Use your custom data
The example above finetune Llama 3.1 on Alpaca dataset ([`yahma/alpaca-cleaned`](https://huggingface.co/datasets/yahma/alpaca-cleaned)), but for real use cases, you may want to finetune it on your own dataset. 

You can do so by specifying the huggingface path to your own dataset as following (we use [`gbharti/finance-alpaca`](https://huggingface.co/datasets/gbharti/finance-alpaca) as an example below):
```bash
# It takes about 1 hour on 8 A100 GPUs to finetune a 8B
# Llama3.1 model with LoRA on finance dataset.
sky launch -c llama31 lora.yaml \
  --secret HF_TOKEN  --env MODEL_SIZE=8B \
  --env CHECKPOINT_BUCKET_NAME="your-own-bucket-name" \
  --env DATASET="gbharti/finance-alpaca"
```

<figure>
<center>
<img src="https://i.imgur.com/B7Ib4Ii.png" width="60%" />

     
<figcaption>Training Loss of LoRA finetuning Llama 3.1</figcaption>
</figure>

## Serve the fine tuned model

With a finetuned Llama 3.1 trained on your dataset, you can now serve the finetuned model with a single command:

> Note: `CHECKPOINT_BUCKET_NAME` should be the bucket you used for storing checkpoints in the previous finetuning step.

```bash
sky launch -c serve-llama31 serve.yaml \
  --env LORA_NAME="my-finance-lora" \
  --env CHECKPOINT_BUCEKT_NAME="your-own-bucket-name"
```

You can interact with the model in a terminal:
```console
ENDPOINT=$(sky status --endpoint 8081 serve-llama31)
curl http://$ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "my-finance-lora",
    "messages": [
      {
        "role": "system",
        "content": "You are a helpful assistant."
      },
      {
        "role": "user",
        "content": "For a car, what scams can be plotted with 0% financing vs rebate?"
      }
    ]
  }' | jq .
```

:tada: **Congratulations!** You now have a finetuned Llama 3.1 8B model that is well versed in finance topics. To recap, all model checkpoints and replicas **stay in your own private infrastructure**.

<details>
    <summary>SkyPilot YAML <code>serve.yaml</code> for serving the finetuned model</summary>
    
```yaml
# Serve a LoRA finetuned Meta Llama 3.1.
#
# Usage:
#
#  HF_TOKEN=xxx sky launch serve.yaml -c llama31-serve --secret HF_TOKEN

envs:
  MODEL_SIZE: 8B
  HF_TOKEN:
  # Change this to your checkpoint bucket created in lora.yaml
  CHECKPOINT_BUCKET_NAME: your-checkpoint-bucket
  LORA_NAME: my-finance-lora

resources:
  accelerators: L4
  ports: 8081
  cpus: 32+

file_mounts:
  /checkpoints:
    name: $CHECKPOINT_BUCKET_NAME
    mode: MOUNT

setup: |
  pip install vllm==0.5.3post1
  pip install vllm-flash-attn==2.5.9.post1
  pip install openai

run: |
  vllm serve meta-llama/Meta-Llama-3.1-${MODEL_SIZE}-Instruct \
    --tensor-parallel-size $SKYPILOT_NUM_GPUS_PER_NODE --enable-lora \
    --lora-modules $LORA_NAME=/checkpoints/${MODEL_SIZE}-lora/Meta-Llama-3.1-${MODEL_SIZE}-Instruct/ \
    --max-model-len=2048 --port 8081
```
    
</details>

## Appendix: Preparation
1. Request the access to [Llama 3.1 weights on huggingface](https://huggingface.co/meta-llama/Meta-Llama-3.1-8B-Instruct) (Click on the blue box and follow the steps):
![](https://i.imgur.com/snIQhr9.png)

2. Get your [huggingface access token](https://huggingface.co/settings/tokens):
![](https://i.imgur.com/3idBgHn.png)


3. Add huggingface token to your environment variable:
```bash
export HF_TOKEN="xxxx"
```

4. Install SkyPilot for launching the finetuning:
```bash
pip install skypilot-nightly[aws,gcp,kubernetes] 
# or other clouds (12 clouds + kubernetes supported) you have setup
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
