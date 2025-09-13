<!-- $REMOVE -->
# vLLM: Easy, Fast, and Cheap LLM Serving with PagedAttention
<!-- $END_REMOVE -->
<!-- $UNCOMMENT# vLLM: Easy, Fast, and Cheap LLM Inference -->

<p align="center">
    <img src="https://i.imgur.com/yxtzPEu.png" alt="vLLM"/>
</p>

This README contains instructions to run a demo for vLLM, an open-source library for fast LLM inference and serving, which improves the throughput compared to HuggingFace by **up to 24x**.

* [Blog post](https://blog.skypilot.co/serving-llm-24x-faster-on-the-cloud-with-vllm-and-skypilot/)
* [Repo](https://github.com/vllm-project/vllm)

## Prerequisites
Install the latest SkyPilot and check your setup of the cloud credentials:
```bash
pip install git+https://github.com/skypilot-org/skypilot.git
sky check
```
See the vLLM SkyPilot [YAMLs](https://github.com/skypilot-org/skypilot/tree/master/llm/vllm).


## Serving Llama-2 with vLLM's OpenAI-compatible API server

Before you get started, you need to have access to the Llama-2 model weights on huggingface. Please check the prerequisites section in [Llama-2 example](https://github.com/skypilot-org/skypilot/tree/master/llm/llama-2/README.md#pre-requisites) for more details.

1. Start serving the Llama-2 model:
```bash
sky launch -c vllm-llama2 serve-openai-api.yaml --secret HF_TOKEN=YOUR_HUGGING_FACE_API_TOKEN
```
**Optional**: Only GCP offers the specified L4 GPUs currently. To use other clouds, use the `--gpus` flag to request other GPUs. For example, to use H100 GPUs:
```bash
sky launch -c vllm-llama2 serve-openai-api.yaml --gpus H100:1 --secret HF_TOKEN=YOUR_HUGGING_FACE_API_TOKEN
```
**Tip**: You can also use the vLLM docker container for faster setup. Refer to [serve-openai-api-docker.yaml](https://github.com/skypilot-org/skypilot/tree/master/llm/vllm/serve-openai-api-docker.yaml) for more.

2. Check the IP for the cluster with:
```
IP=$(sky status --ip vllm-llama2)
```
3. You can now use the OpenAI API to interact with the model.
  - Query the models hosted on the cluster:
```bash
curl http://$IP:8000/v1/models
```
  - Query a model with input prompts for text completion:
```bash
curl http://$IP:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "meta-llama/Llama-2-7b-chat-hf",
      "prompt": "San Francisco is a",
      "max_tokens": 7,
      "temperature": 0
  }'
```
  You should get a similar response as the following:
```console
{
    "id":"cmpl-50a231f7f06a4115a1e4bd38c589cd8f",
    "object":"text_completion","created":1692427390,
    "model":"meta-llama/Llama-2-7b-chat-hf",
    "choices":[{
        "index":0,
        "text":"city in Northern California that is known",
        "logprobs":null,"finish_reason":"length"
    }],
    "usage":{"prompt_tokens":5,"total_tokens":12,"completion_tokens":7}
}
```
  - Query a model with input prompts for chat completion:
```bash
curl http://$IP:8000/v1/chat/completions \
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

## Serving Llama-2 with vLLM for more traffic using SkyServe
To scale up the model serving for more traffic, we introduced SkyServe to enable a user to easily deploy multiple replica of the model:
1. Adding an `service` section in the above `serve-openai-api.yaml` file to make it an [`SkyServe Service YAML`](https://docs.skypilot.co/en/latest/serving/service-yaml-spec.html):

```yaml
# The newly-added `service` section to the `serve-openai-api.yaml` file.
service:
  # Specifying the path to the endpoint to check the readiness of the service.
  readiness_probe: /v1/models
  # How many replicas to manage.
  replicas: 2
```

The entire Service YAML can be found here: [service.yaml](https://github.com/skypilot-org/skypilot/tree/master/llm/vllm/service.yaml).

2. Start serving by using [SkyServe](https://docs.skypilot.co/en/latest/serving/sky-serve.html) CLI:
```bash
sky serve up -n vllm-llama2 service.yaml
```

3. Use `sky serve status` to check the status of the serving:
```bash
sky serve status vllm-llama2
```

You should get a similar output as the following:

```console
Services
NAME           UPTIME     STATUS    REPLICAS   ENDPOINT
vllm-llama2    7m 43s     READY     2/2        3.84.15.251:30001

Service Replicas
SERVICE_NAME   ID   IP             LAUNCHED       RESOURCES          STATUS  REGION
vllm-llama2    1    34.66.255.4    11 mins ago    1x GCP({'L4': 1})  READY   us-central1
vllm-llama2    2    35.221.37.64   15 mins ago    1x GCP({'L4': 1})  READY   us-east4
```

4. Check the endpoint of the service:
```bash
ENDPOINT=$(sky serve status --endpoint vllm-llama2)
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

Notice that it is the same with previously curl command. You should get a similar response as the following:

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

## Serving Mistral AI's Mixtral 8x7b model with vLLM

Please refer to the [Mixtral 8x7b example](https://github.com/skypilot-org/skypilot/tree/master/llm/mixtral) for more details.

