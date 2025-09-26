# Nvidia Dynamo + SkyPilot Demo

This demo shows how to serve models using [Nvidia Dynamo](https://github.com/ai-dynamo/dynamo) and [SkyPilot](https://docs.skypilot.co/en/latest/docs/index.html).

## What is Nvidia Dynamo?

NVIDIA Dynamo is a high-performance inference framework designed for serving generative AI and reasoning models in multi-node distributed environments. Built in Rust for performance and Python for extensibility, Dynamo solves the computational challenges of large language models that exceed single GPU capabilities.

### Core Features
- **Disaggregated Prefill & Decode**: Separates inference phases for optimal resource utilization
- **Dynamic GPU Scheduling**: Intelligent workload distribution across available GPUs
- **LLM-Aware Request Routing**: Smart routing based on model characteristics and cache states
- **Accelerated Data Transfer**: High-performance data movement between nodes
- **KV Cache Offloading**: Multi-tiered memory management for efficient cache utilization

### Key Benefits
- **Unified Multi-GPU Experience**: Makes distributed GPU inference feel like "one accelerator"
- **Maximum GPU Throughput**: Optimizes performance across fluctuating computational demands
- **Tensor Parallelism**: Enables models to span multiple GPUs and servers seamlessly
- **Multi-Engine Support**: Works with TensorRT-LLM, vLLM, SGLang, and other backends
- **Orchestration Excellence**: Bridges the "orchestration gap" in multi-GPU/multi-node environments

## Features Enabled in These Examples

### Single-Node Example (`nvidia-dynamo.sky.yaml`)
- ✅ **SGLang Backend**: High-performance inference engine
- ✅ **OpenAI-Compatible API**: Drop-in replacement for OpenAI endpoints
- ✅ **Basic Load Balancing**: Round-robin request distribution
- ✅ **Auto-Discovery**: Dynamic worker registration

### Multi-Node Example (`nvidia-dynamo-multinode.sky.yaml`)
- ✅ **KV-Aware Routing**: Intelligent cache-based request routing (`--router-mode kv`)
- ✅ **Multi-Node Distribution**: 2 nodes × 8 H100 GPUs (16 total GPUs)
- ✅ **Data Parallel Attention**: DP=2 across nodes (`--enable-dp-attention`)
- ✅ **Tensor Parallelism**: TP=8 per node for large model support
- ✅ **Disaggregated Transfer**: NIXL backend for KV cache transfers

**Model**: `Qwen/Qwen3-8B` (8B parameter reasoning model)
**Architecture**: 2 nodes, each with 8×H100 GPUs, TP=8, DP=2

## Launch Cluster

```bash
sky launch -c dynamo-serving nvidia-dynamo.sky.yaml
```

## Test Endpoint

```bash
export ENDPOINT=$(sky status --endpoint 8080 dynamo-serving)

curl http://$ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-8B",
    "messages": [
    {
        "role": "user",
        "content": "Hello, how are you?"
    }
    ],
    "stream":false,
    "max_tokens": 300
  }' | jq
...
{
  "id": "chatcmpl-e2b5b2bd-59fb-4321-8afc-3b5bb4a717a7",
  "choices": [
    {
      "index": 0,
      "message": {
        "content": "<think>\nOkay, the user greeted me with \"Hello, how are you?\" I should respond in a friendly and natural way. Let me think about the appropriate response.\n\nFirst, I need to acknowledge their greeting. Maybe start with a cheerful \"Hello!\" to match their tone. Then, I should mention that I'm just a virtual assistant, so I don't have feelings, but I'm here to help. It's important to keep it conversational.\n\nI should make sure to invite them to ask questions or share what they need help with. That way, it's open-ended and encourages further interaction. Also, adding an emoji like 😊 can make the response more friendly and approachable.\n\nWait, should I mention my name again? Maybe not necessary since the user already knows. Just keep it simple and welcoming. Let me check the example response they provided. Yes, it's similar to that. I think that's all. Keep the tone positive and helpful.\n</think>\n\nHello! 😊 I'm just a virtual assistant, so I don't have feelings, but I'm here to help you with whatever you need! What can I assist you with today?",
        "role": "assistant",
        "reasoning_content": null
      },
      "finish_reason": "stop"
    }
  ],
  "created": 1758497220,
  "model": "Qwen/Qwen3-8B",
  "object": "chat.completion",
  "usage": {
    "prompt_tokens": 14,
    "completion_tokens": 235,
    "total_tokens": 249
  }
}
```

## Multi-Node Serving

### Launch Multi-Node Cluster

```bash
sky launch -c dynamo-serving-multi nvidia-dynamo-multinode.sky.yaml
```

### Test Multi-Node Endpoint

```bash
export ENDPOINT=$(sky status --endpoint 8080 dynamo-serving-multi)

curl http://$ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-8B",
    "messages": [
    {
        "role": "user",
        "content": "Hello, how are you?"
    }
    ],
    "stream":false,
    "max_tokens": 300
  }' | jq
```

Example output:
```json
{
  "id": "chatcmpl-5524560e-aecd-4b63-a41b-23d0a787c9b0",
  "choices": [
    {
      "index": 0,
      "message": {
        "content": "<think>\nOkay, the user greeted me with \"Hello, how are you?\" I need to respond appropriately. Let me start by acknowledging their greeting. I should mention that I'm an AI assistant, so I don't have feelings, but I'm here to help.\n\nI should keep the response friendly and open-ended. Maybe ask them how they're doing to encourage a conversation. Let me check if there's anything specific they might need. Oh, maybe they have a question or need assistance with something. I should make sure to invite them to ask for help if needed. Also, keep the tone positive and approachable. Alright, putting it all together now.\n</think>\n\nHello! I'm just a virtual assistant, so I don't have feelings, but I'm here and ready to help! How are you today? 😊 If you have any questions or need assistance, feel free to ask!",
        "role": "assistant",
        "reasoning_content": null
      },
      "finish_reason": "stop"
    }
  ],
  "created": 1758501329,
  "model": "Qwen/Qwen3-8B",
  "object": "chat.completion",
  "usage": {
    "prompt_tokens": 14,
    "completion_tokens": 181,
    "total_tokens": 195
  }
}
```

## Verifying KV-Aware Routing

Check logs for these indicators:

```
INFO dynamo_llm::kv_router: KV Routing initialized
INFO dynamo_llm::kv_router::scheduler: Formula for 7587889683284143912 with 0 cached blocks: 0.875 = 1.0 * prefill_blocks + decode_blocks = 1.0 * 0.875 + 0.000
INFO dynamo_llm::kv_router::scheduler: Selected worker: 7587889683284143912, logit: 0.875, cached blocks: 0, total blocks: 109815
```

The routing formula shows worker selection based on KV cache hits and load balancing.