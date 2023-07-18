# Run LLaMA v2 LLM on any cloud with one click

The latest release of LLaMA v2 has been released with promising performance recently.

[LLaMA v2 release](https://github.com/facebookresearch/llama/tree/main)
[LLaMA v2 paper](https://ai.meta.com/research/publications/llama-2-open-foundation-and-fine-tuned-chat-models/)


## How to run LLaMA v2 chatbot?

You can now host your own LLaMA v2 chatbot with SkyPilot using 1-click.

###  Step 1: Apply for the access to the LLaMA v2 model

Go to the [application page](https://ai.meta.com/resources/models-and-libraries/llama-downloads/) and apply for the access to the model weights.


### Step 2: Get the access token from huggingface

Generate a read-only access token on huggingface [here](https://huggingface.co/settings/token), and make sure your huggingface account can access the LLaMA v2 models [here](https://huggingface.co/meta-llama/Llama-2-7b-chat/tree/main).

### Step 3: Deploy the model on SkyPilot

1. Launch the LLaMA v2 chatbot on the cloud:

    ```bash
    sky launch -c llama-v2-chatbot chatbot.yaml
    ```

2. Open another terminal and run:

    ```bash
    ssh -L 7681:localhost:7681 llama
    ```

3. Open http://localhost:7681 in your browser and start chatting!
<img src="https://imgur.com/Ay8sDhG.png" alt="LLaMA chatbot running on the cloud via SkyPilot"/>
