# Run and Serve Gemma3 with SkyPilot and vLLM

> SkyPilot is a framework for running AI and batch workloads on any infra, offering unified execution, high cost savings, and high GPU availability.

On Mar 12, 2025, Google released [Gemma 3](https://blog.google/technology/developers/gemma-3/), a collection of lightweight, state-of-the-art open models built from the same research and technology that powers Google's Gemini 2.0 models.

Gemma 3 is available in four sizes (1B, 4B, 12B, and 27B) and introduces these key capabilities:
- **Multimodal capabilities**: Support for vision-language input with text output (1B, 4B, 12B, and 27B models)
- **128K token context window**: 16x larger input context for analyzing more complex data
- **Multilingual support**: Over 140 languages supported across all model sizes
- **Advanced features**: Function calling, structured output, and improved reasoning capabilities
- **Optimized for devices**: Can run on a range of hardware from single GPUs to larger clusters

In preliminary evaluations, Gemma 3 outperforms many larger models in its size class, making it ideal for a wide range of applications from personal projects to production systems.

This guide walks through how to run and host Gemma 3 models **on any infrastructure** ranging from local GPU workstations, Kubernetes clusters, and public clouds ([15+ clouds supported](https://docs.skypilot.co/en/latest/getting-started/installation.html)).

SkyPilot supports a variety of LLM frameworks and models. In this guide, we use [vLLM](https://github.com/vllm-project/vllm), an open-source library for fast LLM inference and serving, as an example.

**Note**: The provided YAML configuration is set up for the Gemma 3 4B instruction-tuned model, but can be easily modified for other Gemma 3 variants.

## Step 0: Setup

Install SkyPilot on your local machine:

```bash
pip install 'skypilot-nightly[all]'
```


To set up and verify your infra, run 

```bash
sky check 
```
See [docs](https://docs.skypilot.co/en/latest/getting-started/installation.html) for details.


You also need to set up your huggingface account and the [Gemma3 usage agreement](http://huggingface.co/google/gemma-3-4b-pt). 

## Step 1: Run it with SkyPilot

Now it's time to run deepseek with SkyPilot. The instruction can be dependent on your existing hardware.  

8B: 
```
sky launch gemma3.yaml \
  -c deepseek \
  --env MODEL_NAME= google/gemma-3-4b-it \
  --gpus L4:1
  --env HF_TOKEN=xxxx
```

## Step 2: Get Results 
Get a single endpoint that load-balances across replicas:

```
export ENDPOINT=$(sky status --ip gemma-3)
```

Query the endpoint in a terminal:
```
curl http://$ENDPOINT:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "google/gemma-3-4b-it",
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

Get response 
```
{
  "id": "chatcmpl-525ee12bfe3a456b9e764639724e1095",
  "object": "chat.completion",
  "created": 1741805203,
  "model": "google/gemma-3-4b-it",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "reasoning_content": null,
        "content": "I'm Gemma, a large language model created by the Gemma team at Google DeepMind. I’m an open-weights model, which means I’m widely available for public use! \n\nI can take text and images as inputs and generate text-based responses. \n\nIt’s nice to meet you!",
        "tool_calls": []
      },
      "logprobs": null,
      "finish_reason": "stop",
      "stop_reason": 106
    }
  ],
  "usage": {
    "prompt_tokens": 20,
    "total_tokens": 87,
    "completion_tokens": 67,
    "prompt_tokens_details": null
  },
  "prompt_logprobs": null
}
```