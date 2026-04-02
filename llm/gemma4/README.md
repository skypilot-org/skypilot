
<!-- $REMOVE -->
# Run Gemma 4 on Kubernetes or Any Cloud
<!-- $END_REMOVE -->
<!-- $UNCOMMENT# Gemma 4 -->


[Gemma 4](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/) is Google DeepMind's latest family of open models, released under the Apache 2.0 license.

Gemma 4 is available in four sizes: E2B, E4B, 26B (MoE), and 31B (Dense). Key highlights include:
- **Multimodal capabilities**: Support for text and image input (audio on E2B/E4B models)
- **Up to 256K token context window**: For the 26B and 31B models (128K for E2B/E4B)
- **Multilingual support**: Over 140 languages
- **Mixture-of-Experts**: The 26B model only activates 4B parameters per token, enabling fast inference
- **Advanced features**: Native function calling, structured JSON output, and system instructions for building agents

## Prerequisites

- Check that you have installed SkyPilot ([docs](https://docs.skypilot.co/en/latest/getting-started/installation.html)).
- Check that `sky check` shows clouds or Kubernetes are enabled.

## Run Gemma 4

```bash
sky launch gemma4.yaml -c gemma4
```

The `gemma4.yaml` file is as follows:
```yaml
envs:
  MODEL_NAME: google/gemma-4-26B-A4B-it
  MAX_MODEL_LEN: 131072

resources:
  accelerators: {L4:4, A100:1, A100-80GB:1, H100:1}
  ports: 8081
  disk_tier: best

setup: |
  uv pip install vllm

run: |
  echo 'Starting vllm api server...'

  vllm serve $MODEL_NAME \
    --port 8081 \
    --tensor-parallel-size $SKYPILOT_NUM_GPUS_PER_NODE \
    --max-model-len $MAX_MODEL_LEN
```

You can also use other Gemma 4 variants:
```bash
# 31B Dense (flagship model, requires more GPU memory)
sky launch gemma4.yaml -c gemma4 --env MODEL_NAME=google/gemma-4-31B-it --gpus A100-80GB:1

# E4B (small model, runs on a single L4)
sky launch gemma4.yaml -c gemma4 --env MODEL_NAME=google/gemma-4-E4B-it --gpus L4:1
```


### Chat with Gemma 4 with OpenAI API

To curl `/v1/chat/completions`:
```console
ENDPOINT=$(sky status --endpoint 8081 gemma4)

curl http://$ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "google/gemma-4-26B-A4B-it",
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
sky stop gemma4
```

To shut down all resources:
```console
sky down gemma4
```

## Serving Gemma 4: scaling up with SkyServe


With no change to the YAML, launch a fully managed service on your infra:
```console
sky serve up gemma4.yaml -n gemma4
```

Wait until the service is ready:
```console
watch -n10 sky serve status gemma4
```

<details>
<summary>Example outputs:</summary>

```console
Services
NAME    VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
gemma4  1        35s     READY   2/2       xx.yy.zz.100:30001

Service Replicas
SERVICE_NAME  ID  VERSION  IP            LAUNCHED     RESOURCES                   STATUS  REGION
gemma4        1   1        xx.yy.zz.121  18 mins ago  1x GCP([Spot]{'A100': 1})   READY   us-east4
gemma4        2   1        xx.yy.zz.245  18 mins ago  1x GCP([Spot]{'A100': 1})   READY   us-east4
```
</details>


Get a single endpoint that load-balances across replicas:
```console
ENDPOINT=$(sky serve status --endpoint gemma4)
```

> **Tip:** SkyServe fully manages the lifecycle of your replicas. For example, if a spot replica is preempted, the controller will automatically replace it. This significantly reduces the operational burden while saving costs.

To curl the endpoint:
```console
curl http://$ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "google/gemma-4-26B-A4B-it",
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
sky serve down gemma4
```

See more details in [SkyServe docs](https://docs.skypilot.co/en/latest/serving/sky-serve.html).
