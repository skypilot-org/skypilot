# Run and Serve OpenAI GPT-OSS Models with SkyPilot and vLLM

On August 5, 2025, OpenAI released [GPT-OSS](https://openai.com/open-models/), including two state-of-the-art open-weight language models: `gpt-oss-120b` and `gpt-oss-20b`. These models deliver strong real-world performance at low cost and are available under the flexible Apache 2.0 license.

The `gpt-oss-120b` model achieves near-parity with OpenAI o4-mini on core reasoning benchmarks, while the `gpt-oss-20b` model delivers similar results to OpenAI o3-mini.

This guide walks through how to run and host GPT-OSS models on any infrastructure using SkyPilot and vLLM, from local GPU workstations to Kubernetes clusters and public clouds (16+ clouds supported).

## Step 0: Setup Infrastructure

SkyPilot is a framework for running AI and batch workloads on any infrastructure, offering unified execution, high cost savings, and high GPU availability.

### Install SkyPilot

```bash
pip install 'skypilot-nightly[all]'
```
For more details on how to setup your cloud credentials see [SkyPilot docs](https://docs.skypilot.co).

### Choose Your Infrastructure

```bash
sky check
```

## Step 1: Run GPT-OSS Models

### Basic Deployment

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

## Step 2: Get Results

### Query Your Deployment

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
  --env MODEL_NAME=openai/gpt-oss-120b -y
```

Check service status:
```bash
sky serve status
```

Get service endpoint:
```bash
ENDPOINT=$(sky serve status --endpoint gpt-oss-service)
```

### Custom Configuration

The YAML configuration supports various customizations:

- **Reasoning effort**: Models support low, medium, and high reasoning efforts. The reasoning level can be set in the system prompts, e.g., "Reasoning: high".
- **Context length**: Up to 128k tokens natively supported  
- **Memory optimization**: MXFP4 quantization reduces memory requirements
- **Tool use**: Built-in support for function calling and web browsing

### Integration with Other Tools

The deployed endpoint is OpenAI-compatible, so it works with:
- [**LangChain**](https://www.langchain.com/): For building complex AI applications
- [**OpenAI Agents SDK**](https://openai.github.io/openai-agents-python/): For agentic workflows
- [**llm CLI tool**](https://github.com/simonw/llm): For command-line interactions
- **Any OpenAI-compatible client**: Drop-in replacement

## Configuration File

You can find the complete configuration in [`gpt-oss-vllm.sky.yaml`](./gpt-oss-vllm.sky.yaml):

## Cleanup

To shut down your deployment:

```bash
# For basic deployments
sky down gpt-oss-20b
sky down gpt-oss-120b

# For SkyServe deployments  
sky serve down gpt-oss-service
```
