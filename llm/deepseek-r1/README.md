# Run and Serve DeepSeek-R1 on any Infra 

> SkyPilot is a framework for running AI and batch workloads on any infra, offering unified execution, high cost savings, and high GPU availability.

<p align="center">
    <img src="https://i.imgur.com/yxtzPEu.png" alt="vLLM"/>
</p>

On Jan 19, 2025, DeepSeek AI released the [DeepSeek-R1](https://github.com/deepseek-ai/DeepSeek-R1), including a family of models up to 671B parameters. 

DeepSeek-R1 naturally emerged with numerous powerful and interesting reasoning behaviors. It outperforms **state-of-the-art proprietary models** such as OpenAI-o1-mini and becomes **the first time** an open LLM closely rivals like OpenAI-o1.

This guide walks through how to run and host DeepSeek-R1 models **on any infrastructure** from ranging from Local GPU workstation, Kubernetes cluster and public Clouds ([15+ clouds supported](https://docs.skypilot.co/en/latest/getting-started/installation.html)). 

Skypilot supports a vareity of LLM frameworks and models. In this guide, we use [vLLM](https://github.com/vllm-project/vllm), an open-source library for fast LLM inference and serving, as an example. 

### GPUs required for serving DeepSeek-R1

DeepSeek-R1 comes in different sizes, and each size has different GPU requirements. Here is the model-GPU compatibility matrix (applies to both pretrained and instruction tuned models):

| **GPU**         	| **DeepSeek-R1-Distill-Qwen-7B**  | **DeepSeek-R1-Distill-Llama-70B** 	| **DeepSeek-R1**  	| 
|-----------------	|------------------------------	|------------------------	|------------------------------	|
| **L4:1**        	| ✅, with `--max-model-len 4096` 	| ❌                      	| ❌                            	|
| **L4:8**        	| ✅                            	| ❌                      	| ❌                            	|
| **A100:8**      	| ✅                            	| ✅                      	| ❌                            	|
| **A100-80GB:16** 	| ✅                            	| ✅                      	| ✅, with `--max-model-len 4096` 	|


### Step 0: Bring any infra

Install SkyPilot on your local machine:

```bash
pip install 'skypilot-nightly[all]'
```

Pick one of the following depending on what infra you want to run DeepSeek-R1 on:

**If your local machine is a GPU node**: use this command to up a lightweight kubernetes cluster:

```bash
sky local up
```

**If you have a Kubernetes** (e.g., on-prem, EKS / GKE / AKS / ...) **or Clouds** (15+ clouds are supported):

```bash
sky check 
```
See [docs](https://docs.skypilot.co/en/latest/getting-started/installation.html) for details.


### Step 1: Run it!

Now it's time to run deepseek 
```
sky launch deepseek-r1-vllm.yaml \
  -c deepseek \
  --env HF_TOKEN=YOUR_HUGGING_FACE_API_TOKEN \
  --env MODEL_NAME=deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --gpus L4:1
```
replace the command with your own huggingface token and the GPU that you wish to use. You may run `sky show-gpus` to know what GPU that you have access to. 

Get a single endpoint that load-balances across replicas:

```
ENDPOINT=$(sky status --ip deepseek)
```

Query the endpoint in a terminal:
```
curl http://$ENDPOINT:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
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

This will get get 
```
{
  "id": "chatcmpl-6d6d96d8fa084980b60f4059455fbfc2",
  "object": "chat.completion",
  "created": 1737497896,
  "model": "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "<think>\n\n</think>\n\nGreetings! I'm DeepSeek-R1, an artificial intelligence assistant created by DeepSeek. I'm at your service and would be delighted to assist you with any inquiries or tasks you may have.",
        "tool_calls": []
      },
      "logprobs": null,
      "finish_reason": "stop",
      "stop_reason": null
    }
  ],
  "usage": {
    "prompt_tokens": 13,
    "total_tokens": 57,
    "completion_tokens": 44,
    "prompt_tokens_details": null
  },
  "prompt_logprobs": null
}
```