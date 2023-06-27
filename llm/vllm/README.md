# vLLM: Easy, Fast, and Cheap LLM Serving with PagedAttention

<p align="center">
    <img src="https://imgur.com/wzEByNQ.png" alt="vLLM"/>
</p>

This README contains instructions to run a demo for vLLM, an open-source library for fast LLM inference and serving, which improves the through put of the HuggingFace model by **24x**.

* [Blog post](https://vllm.ai/)
* [Repo](https://github.com/vllm-project/vllm)

## Prerequisites
Install the latest SkyPilot and check your setup of the cloud credentials:
```bash
pip install git+https://github.com/skypilot-org/skypilot.git
sky check
```
See the vLLM SkyPilot YAML for [serving](serve.yaml).



## Serve a model with vLLM by yourself with SkyPilot

1. Start the serving the Vicuna-7B model on a single A100 GPU:
```bash
sky launch -c vLLM-serve -s serve.yaml
```
2. Check the output of the command. There will be a sharable gradio link (like the last line of the following). Open it in your browser to chat with Vicuna.
```
(task, pid=7431) Running on local URL:  http://localhost:8001
(task, pid=7431) INFO 06-27 02:22:33 llm_engine.py:59] Initializing an LLM engine with config: model='lmsys/vicuna-7b-v1.3', dtype=torch.float16, use_dummy_weights=False, download_dir=None, use_np_weights=False, tensor_parallel_size=1, seed=0)
(task, pid=7431) INFO 06-27 02:22:33 tokenizer_utils.py:30] Using the LLaMA fast tokenizer in 'hf-internal-testing/llama-tokenizer' to avoid potential protobuf errors.
(task, pid=7431) Running on public URL: https://a8531352b74d74c7d2.gradio.live
```

<p align="center">
    <img src="https://imgur.com/KW9FKRT.gif" alt="Demo"/>
</p>


3. [Optional] Try other GPUs:
```bash
sky launch -c vicuna-serve-v100 -s serve.yaml --gpus V100
```

4. [Optional] Serve the 13B model instead of the default 7B:
```bash
sky launch -c vicuna-serve -s serve.yaml --env MODEL_NAME=lmsys/vicuna-13b-v1.3
```
