<!-- $REMOVE -->
# SGLang: A Structured Generation Language for LLMs
<!-- $END_REMOVE -->
<!-- $UNCOMMENT# SGLang: A Structured Generation Language -->

<p align="center">
    <img src="https://github.com/skypilot-org/skypilot/assets/6753189/10b23cf8-b9b7-4014-a635-10f30a559e7c" alt="SGLang"/>
</p>


This README contains instructions to run a demo for SGLang, an open-source library for fast and expressive LLM inference and serving with **5x throughput**.

* [Repo](https://github.com/sgl-project/sglang)
* [Blog](https://lmsys.org/blog/2024-01-17-sglang)

## Prerequisites
Install the latest SkyPilot and check your setup of the cloud credentials:
```bash
pip install "skypilot-nightly[all]"
sky check
```

## Serving vision-language model LLaVA with SGLang for more traffic using SkyServe
1. Create a [`SkyServe Service YAML`](https://docs.skypilot.co/en/latest/serving/service-yaml-spec.html) with a  `service` section:

```yaml
service:
  # Specifying the path to the endpoint to check the readiness of the service.
  readiness_probe: /health
  # How many replicas to manage.
  replicas: 2
```

The entire Service YAML can be found here: [llava.yaml](https://github.com/skypilot-org/skypilot/tree/master/llm/sglang/llava.yaml).

2. Start serving by using [SkyServe](https://docs.skypilot.co/en/latest/serving/sky-serve.html) CLI:
```bash
sky serve up -n sglang-llava llava.yaml
```

3. Use `sky serve status` to check the status of the serving:
```bash
sky serve status sglang-llava
```

You should get a similar output as the following:

```console
Services
NAME          VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
sglang-llava  1        8m 16s  READY   2/2       34.32.43.41:30001

Service Replicas
SERVICE_NAME  ID  VERSION  IP              LAUNCHED     RESOURCES          STATUS  REGION
sglang-llava  1   1        34.85.154.76    16 mins ago  1x GCP({'L4': 1})  READY   us-east4
sglang-llava  2   1        34.145.195.253  16 mins ago  1x GCP({'L4': 1})  READY   us-east4
```

4. Check the endpoint of the service:
```bash
ENDPOINT=$(sky serve status --endpoint sglang-llava)
```

4. Once it status is `READY`, you can use the endpoint to talk to the model with both text and image inputs:
<figure align="center">
  <img src="https://raw.githubusercontent.com/sgl-project/sglang/main/examples/frontend_language/quick_start/images/cat.jpeg" alt="" width="50%">
  <figcaption>Input image to the LLaVA model.</figcaption>
</figure>

```bash
curl $ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "liuhaotian/llava-v1.6-vicuna-7b",
    "messages": [
      {
        "role": "user",
        "content": [
            {"type": "text", "text": "Describe this image"},
            {
                "type": "image_url",
                "image_url": {
                    "url": "https://raw.githubusercontent.com/sgl-project/sglang/main/examples/frontend_language/quick_start/images/cat.jpeg"
                }
            }
        ]
      }
    ]
  }'
```

You should get a similar response as the following:

```console
{
  "id": "b044d5f637694d3bba30a2d784441c6c",
  "object": "chat.completion",
  "created": 1707565348,
  "model": "liuhaotian/llava-v1.6-vicuna-7b",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": " This is an image of a cute, anthropomorphized cat character."
    },
    "finish_reason": null
  }],
  "usage": {
    "prompt_tokens": 2188,
    "total_tokens": 2204,
    "completion_tokens": 16
  }
}
```


## Serving Llama-2 with SGLang for more traffic using SkyServe
1. The process is the same as serving LLaVA, but with the model path changed to Llama-2. Below are example commands for reference.

2. Start serving by using [SkyServe](https://docs.skypilot.co/en/latest/serving/sky-serve.html) CLI:
```bash
sky serve up -n sglang-llama2 llama2.yaml --secret HF_TOKEN=<your-huggingface-token>
```
The entire Service YAML can be found here: [llama2.yaml](https://github.com/skypilot-org/skypilot/tree/master/llm/sglang/llama2.yaml).

3. Use `sky serve status` to check the status of the serving:
```bash
sky serve status sglang-llama2
```

You should get a similar output as the following:

```console
Services
NAME           VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
sglang-llama2  1        8m 16s  READY   2/2       34.32.43.41:30001

Service Replicas
SERVICE_NAME   ID  VERSION  IP              LAUNCHED     RESOURCES          STATUS  REGION
sglang-llama2  1   1        34.85.154.76    16 mins ago  1x GCP({'L4': 1})  READY   us-east4
sglang-llama2  2   1        34.145.195.253  16 mins ago  1x GCP({'L4': 1})  READY   us-east4
```

4. Check the endpoint of the service:
```bash
ENDPOINT=$(sky serve status --endpoint sglang-llama2)
```

4. Once it status is `READY`, you can use the endpoint to interact with the model:

```bash
curl $ENDPOINT/v1/chat/completions \
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

## Serving Llama-4 with SGLang
For a community tutorial on how to serve Llama 4 on SGLang (both single-node and multi-node), see [Serving Llama 4 models on Nebius AI Cloud with SkyPilot and SGLang](https://nebius.com/blog/posts/serving-llama-4-skypilot-sglang).
