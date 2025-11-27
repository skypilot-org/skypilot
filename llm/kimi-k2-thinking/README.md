
<!-- $REMOVE -->
# Run Kimi K2 Thinking on Kubernetes or Any Cloud
<!-- $END_REMOVE -->
<!-- $UNCOMMENT# Kimi K2 Thinking -->

[Kimi K2 Thinking](https://huggingface.co/moonshotai/Kimi-K2-Thinking) is an advanced large language model created by [Moonshot AI](https://www.moonshot.ai/).

This recipe shows how to run Kimi K2 Thinking with reasoning capabilities on your Kubernetes or any cloud. It includes two modes:

- **Low Latency (TP8)**: Best for interactive applications requiring quick responses
- **High Throughput (TP8+DCP8)**: Best for batch processing and high-volume serving scenarios


## Prerequisites

- Check that you have installed SkyPilot ([docs](https://docs.skypilot.co/en/latest/getting-started/installation.html)).
- Check that `sky check` shows clouds or Kubernetes is enabled.
- **Note**: This model requires 8x H200 or H20 GPUs.

## Run Kimi K2 Thinking (Low Latency Mode)

For low-latency scenarios, use tensor parallelism:

```bash
sky launch kimi-k2-thinking.sky.yaml -c kimi-k2-thinking
```

`kimi-k2-thinking.sky.yaml` uses **tensor parallelism** across 8 GPUs for optimal low-latency performance.

🎉 **Congratulations!** 🎉 You have now launched the Kimi K2 Thinking LLM with reasoning capabilities on your infra.

## Run Kimi K2 Thinking (High Throughput Mode)

For high-throughput scenarios, use Decode Context Parallel (DCP) for **43% faster token generation** and **26% higher throughput**:

```bash
sky launch kimi-k2-thinking-high-throughput.sky.yaml -c kimi-k2-thinking-ht
```

The `kimi-k2-thinking-high-throughput.sky.yaml` adds `--decode-context-parallel-size 8` to enable DCP:

```yaml
run: |
  echo 'Starting vLLM API server for Kimi-K2-Thinking (High Throughput Mode with DCP)...'
  
  vllm serve $MODEL_NAME \
    --port 8081 \
    --tensor-parallel-size 8 \
    --decode-context-parallel-size 8 \
    --enable-auto-tool-choice \
    --tool-call-parser kimi_k2 \
    --reasoning-parser kimi_k2 \
    --trust-remote-code
```

### DCP Performance Gains

From [vLLM's benchmark](https://docs.vllm.ai/projects/recipes/en/latest/moonshotai/Kimi-K2-Think.html):

| Metric | TP8 (Low Latency) | TP8+DCP8 (High Throughput) | Improvement |
|--------|-------------------|----------------------------|-------------|
| Request Throughput (req/s) | 1.25 | 1.57 | **+25.6%** |
| Output Token Throughput (tok/s) | 485.78 | 695.13 | **+43.1%** |
| Mean TTFT (sec) | 271.2 | 227.8 | **+16.0%** |
| KV Cache Size (tokens) | 715,072 | 5,721,088 | **8x** |

## Chat with Kimi K2 Thinking with OpenAI API

To curl `/v1/chat/completions`:

```bash
ENDPOINT=$(sky status --endpoint 8081 kimi-k2-thinking)

curl http://$ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "moonshotai/Kimi-K2-Thinking",
    "messages": [
      {
        "role": "system",
        "content": "You are a helpful assistant with deep reasoning capabilities."
      },
      {
        "role": "user",
        "content": "Explain how to solve the traveling salesman problem for 10 cities."
      }
    ]
  }' | jq .
```

The model will provide its reasoning process in the response, showing its chain-of-thought approach.

## Clean up resources
To shut down all resources:

```bash
sky down kimi-k2-thinking
```

## Serving Kimi-K2-Thinking: scaling up with SkyServe

With no change to the YAML, launch a fully managed service with autoscaling replicas and load-balancing on your infra:

```bash
sky serve up kimi-k2-thinking.sky.yaml -n kimi-k2-thinking
```

Wait until the service is ready:

```bash
watch -n10 sky serve status kimi-k2-thinking
```

Get a single endpoint that load-balances across replicas:

```bash
ENDPOINT=$(sky serve status --endpoint kimi-k2-thinking)
```

> **Tip:** SkyServe fully manages the lifecycle of your replicas. For example, if a spot replica is preempted, the controller will automatically replace it. This significantly reduces the operational burden while saving costs.

To curl the endpoint:

```bash
curl http://$ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "moonshotai/Kimi-K2-Thinking",
    "messages": [
      {
        "role": "system",
        "content": "You are a helpful assistant with deep reasoning capabilities."
      },
      {
        "role": "user",
        "content": "Design a distributed system for real-time analytics."
      }
    ]
  }' | jq .
```

To shut down all resources:

```bash
sky serve down kimi-k2-thinking
```

See more details in [SkyServe docs](https://docs.skypilot.co/en/latest/serving/sky-serve.html).