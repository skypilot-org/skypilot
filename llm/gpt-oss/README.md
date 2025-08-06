# Run and Serve OpenAI gpt-oss Models with SkyPilot and vLLM

![](https://i.imgur.com/MGkHOTg.png)

On August 5, 2025, OpenAI released [gpt-oss](https://openai.com/open-models/), including two state-of-the-art open-weight language models: `gpt-oss-120b` and `gpt-oss-20b`. These models deliver strong real-world performance at low cost and are available under the flexible Apache 2.0 license.

The `gpt-oss-120b` model achieves near-parity with OpenAI o4-mini on core reasoning benchmarks, while the `gpt-oss-20b` model delivers similar results to OpenAI o3-mini.

This guide walks through how to run and host gpt-oss models on any infrastructure using SkyPilot and vLLM, from local GPU workstations to Kubernetes clusters and public clouds (16+ clouds supported).

![Cloud Logos](https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-dark.png)

## Step 0: Setup infrastructure

SkyPilot is a framework for running AI and batch workloads on any infrastructure, offering unified execution, high cost savings, and high GPU availability.

### Install SkyPilot

```bash
pip install 'skypilot[all]'
```
For more details on how to setup your cloud credentials see [SkyPilot docs](https://docs.skypilot.co).

### Choose your infrastructure

```bash
sky check
```

## Step 1: Run gpt-oss models

### Basic deployment

**For `gpt-oss-20b` (smaller model):**
```bash
sky launch -c gpt-oss-20b gpt-oss-vllm.sky.yaml \
  --env MODEL_NAME=openai/gpt-oss-20b
```

**For `gpt-oss-120b` (larger model):**
```bash
sky launch -c gpt-oss-120b gpt-oss-vllm.sky.yaml \
  --env MODEL_NAME=openai/gpt-oss-120b
```

## Step 2: Get results

### Query your deployment

Get the endpoint:
```bash
ENDPOINT=$(sky status --endpoint 8000 gpt-oss-20b)
# or for 120b model:
# ENDPOINT=$(sky status --endpoint 8000 gpt-oss-120b)
```

### Test with cURL

**Basic completion:**
```bash
curl http://$ENDPOINT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-oss-20b",
    "messages": [
      {
        "role": "system",
        "content": "You are a helpful assistant."
      },
      {
        "role": "user", 
        "content": "Explain what MXFP4 quantization is."
      }
    ]
  }' | jq .
```

### Use with OpenAI SDK

```python
import os
from openai import OpenAI
 
ENDPOINT = os.getenv('ENDPOINT')
client = OpenAI(
    base_url=f"http://{ENDPOINT}/v1",
    api_key="EMPTY"
)
 
result = client.chat.completions.create(
    model="openai/gpt-oss-20b",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Explain what MXFP4 quantization is."}
    ]
)
 
print(result.choices[0].message.content)
```

## Step 3: Scale with SkyServe

For production workloads, use [SkyServe](https://docs.skypilot.co/en/latest/serving/sky-serve.html) for automatic scaling and load balancing:

```bash
sky serve up -n gpt-oss-service gpt-oss-vllm.sky.yaml \
  --env MODEL_NAME=openai/gpt-oss-20b -y
```

Check service status:
```bash
sky serve status
```

Get service endpoint:
```bash
ENDPOINT=$(sky serve status --endpoint gpt-oss-service)
```

### Custom configuration

The YAML configuration supports various customizations:

- **Reasoning effort**: Models support low, medium, and high reasoning efforts. The reasoning level can be set in the system prompts, e.g., "Reasoning: high".
- **Context length**: Up to 128k tokens natively supported  
- **Memory optimization**: MXFP4 quantization reduces memory requirements
- **Tool use**: Built-in support for function calling and web browsing

### Integration with other tools

The deployed endpoint is OpenAI-compatible, so it works with:
- [**LangChain**](https://www.langchain.com/): For building complex AI applications
- [**OpenAI Agents SDK**](https://openai.github.io/openai-agents-python/): For agentic workflows
- [**llm CLI tool**](https://github.com/simonw/llm): For command-line interactions
- **Any OpenAI-compatible client**: Drop-in replacement

## Configuration file

You can find the complete configuration in [`gpt-oss-vllm.sky.yaml`](https://github.com/skypilot-org/skypilot/blob/master/llm/gpt-oss/gpt-oss-vllm.sky.yaml).

## Cleanup

To shut down your deployment:

```bash
# For basic deployments
sky down gpt-oss-20b
sky down gpt-oss-120b

# For SkyServe deployments  
sky serve down gpt-oss-service
```
