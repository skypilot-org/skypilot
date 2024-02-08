# SGLang: Fast and Expressive LLM Inference with RadixAttention for 5x throughput

This README contains instructions to run a demo for SGLang, an open-source library for fast and expressive LLM inference and serving with **5x throughput**.

* [Repo](https://github.com/sgl-project/sglang)

## Prerequisites
Install the latest SkyPilot and check your setup of the cloud credentials:
```bash
pip install "skypilot-nightly[all]"
sky check
```

## Serving Llama-2 with SGLang using SkyServe
1. Create a [`SkyServe Service YAML`](https://skypilot.readthedocs.io/en/latest/serving/service-yaml-spec.html) with a  `service` section:

```yaml
service:
  # Specifying the path to the endpoint to check the readiness of the service.
  readiness_probe: /health
  # How many replicas to manage.
  replicas: 2
```

The entire Service YAML can be found here: [sglang.yaml](sglang.yaml).

2. Start serving by using [SkyServe](https://skypilot.readthedocs.io/en/latest/serving/sky-serve.html) CLI:
```bash
sky serve up -n sglang sglang.yaml
```

3. Use `sky serve status` to check the status of the serving:
```bash
sky serve status sglang
```

You should get a similar output as the following:

```console
Services
NAME    VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
sglang  1        8m 16s  READY   2/2       34.32.43.41:30001

Service Replicas
SERVICE_NAME  ID  VERSION  IP              LAUNCHED     RESOURCES          STATUS  REGION
sglang        1   1        34.85.154.76    16 mins ago  1x GCP({'L4': 1})  READY   us-east4
sglang        2   1        34.145.195.253  16 mins ago  1x GCP({'L4': 1})  READY   us-east4
```

4. Check the endpoint of the service:
```bash
ENDPOINT=$(sky serve status --endpoint sglang)
```

4. Once it status is `READY`, you can use the endpoint to interact with the model:

```bash
curl -L $ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-2-7b-chat-hf",
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
  }'
```

You should get a similar response as the following:

```console
{
  "id": "cmpl-879a58992d704caf80771b4651ff8cb6",
  "object": "chat.completion",
  "created": 1692650569,
  "model": "meta-llama/Llama-2-7b-chat-hf",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": " Hello! I'm just an AI assistant, here to help you"
    },
    "finish_reason": "length"
  }],
  "usage": {
    "prompt_tokens": 31,
    "total_tokens": 47,
    "completion_tokens": 16
  }
}
```
