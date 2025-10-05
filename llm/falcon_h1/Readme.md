# Run and Serve Falcon H1 with SkyPilot and vLLM


On May 21, 2025, Technology Innovation Institute (TII) released [Falcon H1](https://falcon-lm.github.io/blog/falcon-h1/), the latest evolution in the Falcon family of large language models featuring an advanced hybrid architecture that integrates both State Space Models (SSMs) and Attention Mechanisms in every transformer block.

Falcon H1 is available in six sizes (0.5B, 1.5B, 1.5B-Deep, 3B, 7B, and 34B) and introduces these key capabilities:
- **Hybrid architecture**: Parallel SSM + Attention in every layer for enhanced reasoning and long-range memory
- **Multilingual support**: Native training in 18 core languages, scalable to 100+ languages
- **Efficient inference**: Optimized for both edge devices and large-scale deployments
- **State-of-the-art performance**: Outperforms models twice its size in reasoning, maths, and coding tasks
- **Full ecosystem integration**: Compatible with vLLM, Transformers, and llama.cpp out-of-the-box

In benchmarks, Falcon H1 models achieve state-of-the-art performance in most tasks, even outperforming some closed-source models like GPT-4o-mini in coding, reasoning, and instruction-following tasks.

This guide walks through how to run and host Falcon H1 models **on any infrastructure** ranging from local GPU workstations, Kubernetes clusters, and public clouds ([15+ clouds supported](https://docs.skypilot.co/en/latest/getting-started/installation.html)).


**Note**: The provided YAML configuration is set up for the Falcon H1 1.5B instruction-tuned model, but can be easily modified for other Falcon H1 variants.

## Step 0: Install SkyPilot

Install SkyPilot on your local machine:

```bash
pip install 'skypilot-nightly[all]'
```
To set up and verify your infra, run:
```bash
sky check
```
See [docs](https://docs.skypilot.co/en/latest/getting-started/installation.html) for details.

Note: Falcon H1 models are open-source and do not require any HuggingFace token or usage agreement.
## Step 1: Run it with SkyPilot
Now it's time to run Falcon H1 with SkyPilot. The instruction depends on your model size choice:

### For smaller models (0.5B, 1.5B):
```bash
sky launch falcon_h1.yaml \
  -c falcon-h1 \
  --env MODEL_NAME=tiiuae/Falcon-H1-1.5B-Instruct \
  --gpus T4:1
```
### For medium models (3B, 7B):
```bash
sky launch falcon_h1.yaml \
  -c falcon-h1 \
  --env MODEL_NAME=tiiuae/Falcon-H1-3B-Instruct \
  --gpus A10:1
```

### For large model (34B):
```bash
sky launch falcon_h1.yaml \
  -c falcon-h1 \
  --env MODEL_NAME=tiiuae/Falcon-H1-34B-Instruct \
  --gpus A100-80GB:8
```

Important: Falcon H1 uses Mamba with n_groups=2, which requires tensor_parallel_size to be 1 or 2 for optimal performance.

## Step 2: Get Results
Get a single endpoint that load-balances across replicas:
```bash
export ENDPOINT=$(sky status --ip falcon-h1)
```

Query the endpoint in a terminal:
```bash
curl http://$ENDPOINT:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "tiiuae/Falcon-H1-1.5B-Instruct",
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

Get response:
```bash
json{
  "id": "chatcmpl-525ee12bfe3a456b9e764639724e1095",
  "object": "chat.completion",
  "created": 1741805203,
  "model": "tiiuae/Falcon-H1-1.5B-Instruct",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "I am Falcon H1, a large language model created by the Technology Innovation Institute (TII) in Abu Dhabi. I'm built with a hybrid architecture that combines State Space Models and Attention mechanisms, allowing me to excel at reasoning, coding, and multilingual tasks. I'm designed to be helpful, harmless, and honest in my interactions. How can I assist you today?",
        "tool_calls": []
      },
      "logprobs": null,
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 20,
    "total_tokens": 98,
    "completion_tokens": 78
  }
}
```

### Deploy the Service with Multiple Replicas
The launching command above only starts a single replica (with 1 node) for the service. SkyServe helps deploy the service with multiple replicas with out-of-the-box load balancing, autoscaling, and automatic recovery.
Importantly, it also enables serving on spot instances resulting in 30% lower cost.
The only change needed is to add a service section for serving specific configuration:
```yaml
service:
  replicas: 2
  readiness_probe:
    path: /v1/chat/completions
    post_data:
      model: $MODEL_NAME
      messages:
        - role: user
          content: Hello! What is your name?
      max_tokens: 1
```

And run the SkyPilot YAML with a single command:
```bash
sky serve up -n falcon-serve llm/falcon_h1/falcon_h1.yaml
```


💬 [Try it now: Falcon H1 Chat Interface](https://chat.falconllm.tii.ae/)<br>
🤗 [Model Collection: HuggingFace Models](https://huggingface.co/collections/tiiuae/falcon-h1-6819f2795bc406da60fab8df)<br>
📰 [Technical Blog: Falcon H1 Architecture](https://falcon-lm.github.io/blog/falcon-h1/)<br>
🖥️ [Live Demo: HuggingFace Playground](https://huggingface.co/spaces/tiiuae/Falcon-H1-playground)<br>
💬 [Community: Discord Server](https://discord.gg/trwMYP9PYm)