# LoRAX: Multi-LoRA inference server that scales to 1000s of fine-tuned LLMs

<p align="center">
    <img src="https://github.com/predibase/lorax/blob/main/docs/images/lorax_guy.png" alt="LoRAX" style="width:200px;" />
</p>

[LoRAX](https://github.com/predibase/lorax) (LoRA eXchange) is a framework that allows users to serve thousands of fine-tuned LLMs on a single GPU, dramatically reducing the cost of serving without compromising on throughput or latency. It works by dynamically loading multiple fine-tuned "adapters" (LoRAs, etc.) on top of a single base model at runtime. Concurrent requests for different adapters can be processed together in a single batch, allowing LoRAX to maintain near linear throughput scaling as the number of adapters increases.

## Launch a deployment

Create a YAML configuration file called `lorax.yaml`:

```yaml
resources:
  accelerators: {A10G, A10, L4, A100, A100-80GB}
  memory: 32+
  ports: 
    - 8080

envs:
  MODEL_ID: mistralai/Mistral-7B-Instruct-v0.1

run: |
  docker run --gpus all --shm-size 1g -p 8080:80 -v ~/data:/data \
    ghcr.io/predibase/lorax:latest \
    --model-id $MODEL_ID
```

In the above example, we're asking SkyPilot to provision an AWS instance with 1 Nvidia A10G GPU and at least 32GB of RAM. Once the node is provisioned,
SkyPilot will launch the LoRAX server using our latest pre-built [Docker image](https://github.com/predibase/lorax/pkgs/container/lorax).

Let's launch our LoRAX job:

```shell
sky launch -c lorax-cluster lorax.yaml
```

By default, this config will deploy `Mistral-7B-Instruct`, but this can be overridden by running `sky launch` with the argument `--env MODEL_ID=<my_model>`.

**NOTE:** This config will launch the instance on a public IP. It's highly recommended to secure the instance within a private subnet. See the [Advanced Configurations](https://skypilot.readthedocs.io/en/latest/reference/config.html#config-yaml) section of the SkyPilot docs for options to run within VPC and setup private IPs.

## Prompt LoRAX w/ base model

In a separate window, obtain the IP address of the newly created instance:

```shell
sky status --ip lorax-cluster
```

Now we can prompt the base model deployment using a simple REST API:

```shell
IP=$(sky status --ip lorax-cluster)

curl http://$IP:8080/generate \
    -X POST \
    -d '{
        "inputs": "[INST] Natalia sold clips to 48 of her friends in April, and then she sold half as many clips in May. How many clips did Natalia sell altogether in April and May? [/INST]",
        "parameters": {
            "max_new_tokens": 64
        }
    }' \
    -H 'Content-Type: application/json'
```

## Prompt LoRAX w/ adapter

To improve the quality of the response, we can add a single parameter `adapter_id` pointing to a valid LoRA adapter from the [HuggingFace Hub](https://huggingface.co/models).

In this example, we'll use the adapter `vineetsharma/qlora-adapter-Mistral-7B-Instruct-v0.1-gsm8k` that fine-tuned the base model to improve its math reasoning:

```shell
curl http://$IP:8080/generate \
    -X POST \
    -d '{
        "inputs": "[INST] Natalia sold clips to 48 of her friends in April, and then she sold half as many clips in May. How many clips did Natalia sell altogether in April and May? [/INST]",
        "parameters": {
            "max_new_tokens": 64,
            "adapter_id": "vineetsharma/qlora-adapter-Mistral-7B-Instruct-v0.1-gsm8k"
        }
    }' \
    -H 'Content-Type: application/json'
```

Here are some other interesting Mistral-7B fine-tuned models to test out:

- [alignment-handbook/zephyr-7b-dpo-lora](https://huggingface.co/alignment-handbook/zephyr-7b-dpo-lora): Mistral-7b fine-tuned on Zephyr-7B dataset with DPO.
- [IlyaGusev/saiga_mistral_7b_lora](https://huggingface.co/IlyaGusev/saiga_mistral_7b_lora): Russian chatbot based on `Open-Orca/Mistral-7B-OpenOrca`.
- [Undi95/Mistral-7B-roleplay_alpaca-lora](https://huggingface.co/Undi95/Mistral-7B-roleplay_alpaca-lora): Fine-tuned using role-play prompts.

You can find more LoRA adapters [here](https://huggingface.co/models?pipeline_tag=text-generation&sort=trending&search=-lora), or try fine-tuning your own with [PEFT](https://github.com/huggingface/peft) or [Ludwig](https://ludwig.ai).

## Stop the deployment

Stopping the deployment will shut down the instance, but keep the storage volume:

```shell
sky stop lorax-cluster
```

Because we set `docker run ... -v ~/data:/data` in our config from before, this means any model weights or adapters we downloaded will be persisted the next time we run `sky launch`. The LoRAX Docker image will also be cached, meaning tags like `latest` won't be updated on restart unless you add `docker pull` to your `run` configuration.

## Delete the deployment

To completely delete the deployment, including the storage volume:

```shell
sky down lorax-cluster
```

The next time you run `sky launch`, the deployment will be recreated from scratch.
