<!-- $REMOVE -->
# Self-Hosted Llama 2 Chatbot on Any Cloud
<!-- $END_REMOVE -->
<!-- $UNCOMMENT# Llama 2: Open LLM from Meta -->

[Llama-2](https://github.com/facebookresearch/llama/tree/main) is the top open-source models on the [Open LLM leaderboard](https://huggingface.co/spaces/HuggingFaceH4/open_llm_leaderboard) today. It has been released with a license that authorizes commercial use. You can deploy a private Llama-2 chatbot with SkyPilot in your own cloud with just one simple command.

* [Llama-2 release](https://github.com/facebookresearch/llama/tree/main)
* [Llama-2 paper](https://ai.meta.com/research/publications/llama-2-open-foundation-and-fine-tuned-chat-models/)

## Why use SkyPilot to deploy over commercial hosted solutions?

* No lock-in: run on any supported cloud - AWS, Azure, GCP, Lambda Cloud, IBM, Samsung, OCI
* Everything stays in your cloud account (your VMs & buckets)
* No one else sees your chat history
* Pay absolute minimum — no managed solution markups
* Freely choose your own model size, GPU type, number of GPUs, etc, based on scale and budget.

…and you get all of this with 1 click — let SkyPilot automate the infra.

## Pre-requisites

1. Apply for the access to the Llama-2 model

Go to the [application page](https://ai.meta.com/resources/models-and-libraries/llama-downloads/) and apply for the access to the model weights.


2. Get the access token from huggingface

Generate a read-only access token on huggingface [here](https://huggingface.co/settings/token), and make sure your huggingface account can access the Llama-2 models [here](https://huggingface.co/meta-llama/Llama-2-7b-chat/tree/main).

Fill the access token in the [chatbot-hf.yaml](https://github.com/skypilot-org/skypilot/tree/master/llm/llama-2/chatbot-hf.yaml) and [chatbot-meta.yaml](https://github.com/skypilot-org/skypilot/tree/master/llm/llama-2/chatbot-meta.yaml) file.
```yaml
envs:
  MODEL_SIZE: 7
secrets:
  HF_TOKEN: null # Pass with `--secret HF_TOKEN` in CLI
```


## Running your own Llama-2 chatbot with SkyPilot

You can now host your own Llama-2 chatbot with SkyPilot using 1-click.

1. Start serving the LLaMA-7B-Chat 2 model on a single A100 GPU:
```bash
sky launch -c llama-serve -s chatbot-hf.yaml
```
2. Check the output of the command. There will be a sharable gradio link (like the last line of the following). Open it in your browser to chat with Llama-2.
```
(task, pid=20933) 2023-04-12 22:08:49 | INFO | gradio_web_server | Namespace(host='0.0.0.0', port=None, controller_url='http://localhost:21001', concurrency_count=10, model_list_mode='once', share=True, moderate=False)
(task, pid=20933) 2023-04-12 22:08:49 | INFO | stdout | Running on local URL:  http://0.0.0.0:7860
(task, pid=20933) 2023-04-12 22:08:51 | INFO | stdout | Running on public URL: https://<random-hash>.gradio.live
```

<p align="center">
  <img src="https://i.imgur.com/cLqulb0.gif" alt="Llama-2 Demo"/>
</p>

3. **Optional**: Try other GPUs:
```bash
sky launch -c llama-serve-l4 -s chatbot-hf.yaml --gpus L4
```

L4 is the latest generation GPU built for large inference AI workloads. Find more details [here](https://cloud.google.com/blog/products/compute/introducing-g2-vms-with-nvidia-l4-gpus).

4. **Optional**: Serve the 13B model instead of the default 7B:
```bash
sky launch -c llama-serve -s chatbot-hf.yaml --env MODEL_SIZE=13
```

5. **Optional**: Serve the **70B** Llama-2 model:
```bash
sky launch -c llama-serve-70b -s chatbot-hf.yaml --env MODEL_SIZE=70 --gpus A100-80GB:2
```

![70B model](https://i.imgur.com/jEM8w3r.png)


## How to run Llama-2 chatbot with the FAIR model?

You can also host the official FAIR model without using huggingface and gradio.


1. Launch the Llama-2 chatbot on the cloud:

    ```bash
    sky launch -c llama chatbot-meta.yaml
    ```

2. Open another terminal and run:

    ```bash
    ssh -L 7681:localhost:7681 llama
    ```

3. Open http://localhost:7681 in your browser and start chatting!
<img src="https://i.imgur.com/Ay8sDhG.png" alt="LLaMA chatbot running on the cloud via SkyPilot"/>


