
<!-- $REMOVE -->
# Run Llama 4 on Kubernetes or Any Cloud
<!-- $END_REMOVE -->
<!-- $UNCOMMENT# Llama 4 -->


[Llama 4](https://ai.meta.com/blog/llama-4-multimodal-intelligence/) family was released by Meta on Apr 5, 2025.

![](https://i.imgur.com/kjqLX87.png)


## Prerequisites

- Go to the [HuggingFace model page](https://huggingface.co/meta-llama/) and request access to the model [meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8](https://huggingface.co/meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8).
- Check that you have installed SkyPilot ([docs](https://docs.skypilot.co/en/latest/getting-started/installation.html)).
- Check that `sky check` shows clouds or Kubernetes are enabled.

## Run Llama 4

```bash
HF_TOKEN=xxx sky launch llama4.yaml -c llama4 --secret HF_TOKEN
```

https://github.com/user-attachments/assets/48cdc44a-31a5-45f0-93be-7a8b6c6a0ded


The `llama4.yaml` file is as follows:
```yaml
envs:
  MODEL_NAME: meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8

secrets:
  HF_TOKEN: # TODO: Fill with your own huggingface token, or use --secret to pass.

resources:
  accelerators: { H100:8, H200:8, B100:8, B200:8, GB200:8 }
  cpus: 32+
  disk_size: 512  # Ensure model checkpoints can fit.
  disk_tier: best
  ports: 8081  # Expose to internet traffic.

setup: |
  uv pip install vllm==0.8.3

run: |
  echo 'Starting vllm api server...'

  vllm serve $MODEL_NAME \
    --port 8081 \
    --tensor-parallel-size $SKYPILOT_NUM_GPUS_PER_NODE \
    --max-model-len 430000

```

You can use other models by setting different `MODEL_NAME`.
```bash
HF_TOKEN=xxx sky launch llama4.yaml -c llama4 --secret HF_TOKEN --env MODEL_NAME=meta-llama/Llama-4-Scout-17B-16E-Instruct
```


🎉 **Congratulations!** 🎉 You have now launched the Llama 4 Maverick Instruct LLM on your infra.

### Chat with Llama 4 with OpenAI API

To curl `/v1/chat/completions`:
```console
ENDPOINT=$(sky status --endpoint 8081 llama4)

curl http://$ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
    "messages": [
      {
        "role": "system",
        "content": "You are a helpful assistant."
      },
      {
        "role": "user",
        "content": "Who are you?"
      }
    ]
  }' | jq .
```

To stop the instance:
```console
sky stop llama4
```

To shut down all resources:
```console
sky down llama4
```

## Serving Llama-4: scaling up with SkyServe


With no change to the YAML, launch a fully managed service on your infra:
```console
HF_TOKEN=xxx sky serve up llama4.yaml -n llama4 --secret HF_TOKEN
```

Wait until the service is ready:
```console
watch -n10 sky serve status llama4
```

<details>
<summary>Example outputs:</summary>

```console
Services
NAME   VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
llama4 1        35s     READY   2/2       xx.yy.zz.100:30001

Service Replicas
SERVICE_NAME  ID  VERSION  IP            LAUNCHED     RESOURCES                  STATUS  REGION
llama4        1   1        xx.yy.zz.121  18 mins ago  1x GCP([Spot]{'H100': 8})  READY   us-east4
llama4        2   1        xx.yy.zz.245  18 mins ago  1x GCP([Spot]{'H100': 8})  READY   us-east4
```
</details>


Get a single endpoint that load-balances across replicas:
```console
ENDPOINT=$(sky serve status --endpoint llama4)
```

> **Tip:** SkyServe fully manages the lifecycle of your replicas. For example, if a spot replica is preempted, the controller will automatically replace it. This significantly reduces the operational burden while saving costs.

To curl the endpoint:
```console
curl http://$ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
    "messages": [
      {
        "role": "system",
        "content": "You are a helpful assistant."
      },
      {
        "role": "user",
        "content": "Who are you?"
      }
    ]
  }' | jq .
```

To shut down all resources:
```console
sky serve down llama4
```

See more details in [SkyServe docs](https://docs.skypilot.co/en/latest/serving/sky-serve.html).

## Community tutorial: Llama 4 on SGLang, served via SkyServe

For a community tutorial on how to serve Llama 4 on SGLang (both single-node and multi-node), see [Serving Llama 4 models on Nebius AI Cloud with SkyPilot and SGLang](https://nebius.com/blog/posts/serving-llama-4-skypilot-sglang).
