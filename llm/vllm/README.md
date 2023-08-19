# vLLM: Easy, Fast, and Cheap LLM Serving with PagedAttention

<p align="center">
    <img src="https://imgur.com/yxtzPEu.png" alt="vLLM"/>
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
See the vLLM SkyPilot YAML for [serving](serve.yaml).



## Serve a model with vLLM, launched on the cloud by SkyPilot

1. Start the serving the LLaMA-65B model on 8 A100 GPUs:
```bash
sky launch -c vllm-serve -s serve.yaml
```
2. Check the output of the command. There will be a sharable gradio link (like the last line of the following). Open it in your browser to use the LLaMA model to do the text completion.
```
(task, pid=7431) Running on public URL: https://a8531352b74d74c7d2.gradio.live
```

<p align="center">
    <img src="https://imgur.com/YUaqWrJ.gif" alt="Demo"/>
</p>


3. **Optional**: Serve the 13B model instead of the default 65B and use less GPU:
```bash
sky launch -c vllm-serve -s serve.yaml --gpus A100:1 --env MODEL_NAME=decapoda-research/llama-13b-hf
```

## Serve Llama-2 with vLLM's OpenAI API compatibility server

Before you get started, you need to have the access to the Llama-2 model weights on huggingface. Please check the prerequisites section in [Llama-2 example](../llama-2/README.md#pre-requisites) for more details.

1. Start the serving the Llama-2 model:
```bash
sky launch -c vllm-llama2 serving-openai-api.yaml
```
2. Check the IP for the cluster with:
```
sky status -a
```
3. You can now use the OpenAI API to interact with the model. 
  - you can use the following completion API to query the models hosted on the cluster:
```bash
curl http://<IP>:8000/v1/models
```
  - you can use the following completion API to query the models hosted on the cluster:
```bash
curl http://<IP>:8000/v1/completions \
-H "Content-Type: application/json" \
-d '{
"model": "meta-llama/Llama-2-7b-chat-hf",
"prompt": "San Francisco is a",
"max_tokens": 7,
"temperature": 0
}'
```
  You should get similar response as the following:
```
{"id":"cmpl-50a231f7f06a4115a1e4bd38c589cd8f","object":"text_completion","created":1692427390,"model":"meta-llama/Llama-2-7b-chat-hf","choices":[{"index":0,"text":"city in Northern California that is known","logprobs":null,"finish_reason":"length"}],"usage":{"prompt_tokens":5,"total_tokens":12,"completion_tokens":7}}
```
