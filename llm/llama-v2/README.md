# Run LLaMA v2 LLM on any cloud with one click

The latest release of LLaMA v2 has been released with promising performance recently.

[LLaMA v2 release](https://github.com/facebookresearch/llama/tree/main)
[LLaMA v2 paper](https://ai.meta.com/research/publications/llama-2-open-foundation-and-fine-tuned-chat-models/)


## Pre-requisites

###  Step 1: Apply for the access to the LLaMA v2 model

Go to the [application page](https://ai.meta.com/resources/models-and-libraries/llama-downloads/) and apply for the access to the model weights.


### Step 2: Get the access token from huggingface

Generate a read-only access token on huggingface [here](https://huggingface.co/settings/token), and make sure your huggingface account can access the LLaMA v2 models [here](https://huggingface.co/meta-llama/Llama-2-7b-chat/tree/main).

Fill the access token in the [chatbot-hf.yaml](chatbot-hf.yaml) and [chatbot-meta.yaml](chatbot-meta.yaml) file.
```yaml
envs:
  MODEL_SIZE: 7
  HF_TOKEN: <your-huggingface-token>
```

## How to run LLaMA v2 chatbot (Huggingface model)?

You can now host your own LLaMA v2 chatbot with SkyPilot using 1-click.

1. Start the serving the LLaMA-7B-Chat v2 model on a single A100 GPU:
```bash
sky launch -c llama-serve -s chatbot-hf.yaml
```
2. Check the output of the command. There will be a sharable gradio link (like the last line of the following). Open it in your browser to chat with Vicuna.
```
(task, pid=20933) 2023-04-12 22:08:49 | INFO | gradio_web_server | Namespace(host='0.0.0.0', port=None, controller_url='http://localhost:21001', concurrency_count=10, model_list_mode='once', share=True, moderate=False)
(task, pid=20933) 2023-04-12 22:08:49 | INFO | stdout | Running on local URL:  http://0.0.0.0:7860
(task, pid=20933) 2023-04-12 22:08:51 | INFO | stdout | Running on public URL: https://<random-hash>.gradio.live
```

3. [Optional] Try other GPUs:
```bash
sky launch -c llama-serve-v100 -s chatbot-hf.yaml --gpus V100
```

4. [Optional] Serve the 13B model instead of the default 7B:
```bash
sky launch -c llama-serve -s chatbot-hf.yaml --env MODEL_SIZE=13
```


## How to run LLaMA v2 chatbot (FAIR model)?

You can now host your own LLaMA v2 chatbot with SkyPilot using 1-click.


1. Launch the LLaMA v2 chatbot on the cloud:

    ```bash
    sky launch -c llama chatbot-meta.yaml
    ```

2. Open another terminal and run:

    ```bash
    ssh -L 7681:localhost:7681 llama
    ```

3. Open http://localhost:7681 in your browser and start chatting!
<img src="https://imgur.com/Ay8sDhG.png" alt="LLaMA chatbot running on the cloud via SkyPilot"/>


