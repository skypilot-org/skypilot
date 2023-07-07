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
