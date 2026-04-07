<!-- $REMOVE -->
# Serving models with vLLM on Kubernetes and clouds with SkyPilot
<!-- $END_REMOVE -->
<!-- $UNCOMMENT# vLLM: Easy, Fast, and Cheap LLM Inference -->

<p align="center">
    <img src="https://i.imgur.com/yxtzPEu.png" alt="vLLM"/>
</p>

[vLLM](https://github.com/vllm-project/vllm) is an open-source inference engine for fast LLM serving. You can use it to serve your models on Kubernetes and clouds with SkyPilot.

## Prerequisites
Install the latest SkyPilot and check your setup of the cloud credentials:
```bash
pip install git+https://github.com/skypilot-org/skypilot.git
sky check
```
See the vLLM SkyPilot [YAMLs](https://github.com/skypilot-org/skypilot/tree/master/llm/vllm).


## Serving Llama 4 with vLLM's OpenAI-Compatible API server

Before you get started, you need to have access to the Llama 4 model weights on huggingface. Please check the prerequisites section in [Llama 4 example](https://github.com/skypilot-org/skypilot/tree/master/llm/llama-4/README.md#prerequisites) for more details.

1. Start serving the Llama 4 model:
```bash
HF_TOKEN=xxx sky launch -c vllm-llama4 vllm.sky.yaml --secret HF_TOKEN
```

Wait for the launch to complete (you will see `Application startup complete` in the logs).

**Optional**: To use different GPU types, use the `--gpus` flag to request other GPUs. For example, to use H200 GPUs:
```bash
HF_TOKEN=xxx sky launch -c vllm-llama4 vllm.sky.yaml --gpus H200:8 --secret HF_TOKEN
```
**Tip**: You can also use the vLLM docker container for faster cold starts by setting the `image_id` in the `resources` section. Refer to [vllm.sky.yaml](https://github.com/skypilot-org/skypilot/tree/master/llm/vllm/vllm.sky.yaml) for more.

2. Get the endpoint for the cluster:
```bash
ENDPOINT=$(sky status --endpoint 8081 vllm-llama4)
```

3. You can now use the OpenAI API to interact with the model.
  - Query the models hosted on the cluster:
```bash
curl http://$ENDPOINT/v1/models
```
  - Query a model with input prompts for chat completion:
  ```bash
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
    }'
  ```
  Example response:
  ```console
  {
    "id": "chatcmpl-eba4cf3d5c3246f2a2160bc4b7caecba",
    "object": "chat.completion",
    "model": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
    "choices": [
      {
        "index": 0,
        "message": {
          "role": "assistant",
          "content": "I'm an AI assistant designed by Meta. I'm here to answer your questions, share interesting ideas and maybe even surprise you with a fresh perspective. What's on your mind?",
        },
        "finish_reason": "stop",
        "stop_reason": null
      }
    ],
    "usage": {
      "prompt_tokens": 25,
      "total_tokens": 59,
      "completion_tokens": 34,
      "prompt_tokens_details": null
    },
  }
  ```

## Scaling up vLLM with SkyServe
To scale up model serving for more traffic, use SkyServe to easily deploy multiple replicas, load balance, autoscale, and more.

1. The `service` section in the above `vllm.sky.yaml` file describes the number of replicas to deploy and the readiness probe:

```yaml
service:
  replicas: 2
  # An actual request for readiness probe.
  readiness_probe:
    path: /v1/chat/completions
    post_data:
      model: $MODEL_NAME
      messages:
        - role: user
          content: Hello! What is your name?
      max_tokens: 1
```

2. Launch the service and it's replicas by using [SkyServe](https://docs.skypilot.co/en/latest/serving/sky-serve.html) CLI:
```bash
HF_TOKEN=xxx sky serve up -n vllm-llama4 vllm.sky.yaml --secret HF_TOKEN
```

3. Use `sky serve status` to check the status of the service:
```bash
sky serve status vllm-llama4
```

You should get a similar output as the following:

```console
Services
NAME           UPTIME     STATUS    REPLICAS   ENDPOINT
vllm-llama4    7m 43s     READY     2/2        3.84.15.251:30001

Service Replicas
SERVICE_NAME   ID   IP             LAUNCHED       RESOURCES              STATUS  REGION
vllm-llama4    1    34.66.255.4    11 mins ago    1x GCP({'H100': 8})    READY   us-central1
vllm-llama4    2    35.221.37.64   15 mins ago    1x GCP({'H100': 8})    READY   us-east4
```

4. Check the endpoint of the service:
```bash
ENDPOINT=$(sky serve status --endpoint vllm-llama4)
```

5. Once it status is `READY`, you can use the endpoint to interact with the model:

```bash
curl $ENDPOINT/v1/chat/completions \
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
  }'
```

Notice that it is the same with previously curl command. You should get a similar response as the following:

```console
{
  "id": "cmpl-879a58992d704caf80771b4651ff8cb6",
  "object": "chat.completion",
  "created": 1692650569,
  "model": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
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

## Multi-node serving with vLLM

For large models that require multiple nodes, vLLM supports pipeline parallelism and tensor parallelism across nodes. This is useful for serving very large models that may not fit on a single node.

SkyPilot makes it easy to launch and manage multi-node vLLM deployments. It takes care of:

1. Provisioning multiple nodes (``num_nodes: n`` flag in the YAML)
2. Distributed setup (including setting up the Ray cluster) and launching the vLLM server.
3. Load balancing and autoscaling across replicas.

Refer to [vllm-multinode.sky.yaml](https://github.com/skypilot-org/skypilot/tree/master/llm/vllm/vllm-multinode.sky.yaml) for more details.

1. Start a multi-node vLLM service:
```bash
HF_TOKEN=xxx sky launch -c vllm-multinode vllm-multinode.sky.yaml --secret HF_TOKEN
```

2. Get the endpoint:
```bash
ENDPOINT=$(sky status --endpoint 8081 vllm-multinode)
```

3. Test the multi-node service:
```bash
curl $ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
    "messages": [
      {
        "role": "user",
        "content": "Explain quantum computing in simple terms"
      }
    ],
    "max_tokens": 100
  }'
```

4. To scale up to multiple replicas, use SkyServe:
```bash
HF_TOKEN=xxx sky serve up -n vllm-multinode vllm-multinode.sky.yaml --secret HF_TOKEN
# After the service is ready, get the endpoint
ENDPOINT=$(sky serve status --endpoint vllm-multinode)
```

The multi-node setup automatically distributes the model across nodes using pipeline parallelism and tensor parallelism, allowing you to serve larger models or achieve higher throughput.