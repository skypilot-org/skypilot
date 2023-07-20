# Train Your Own Vicuna on Llama-2

[LLaMA 2](https://github.com/facebookresearch/llama/tree/main) is the top open-source models on the [Open LLM leaderboard](https://huggingface.co/spaces/HuggingFaceH4/open_llm_leaderboard) today. It has been released with a license that authorizes commercial use.

With the license, you can now train your own Vicuna on LLaMA 2 for commercial use. This is a step-by-step guide to train your own Vicuna on LLaMA 2.

## Pre-requisites

1. Apply for the access to the LLaMA 2 model

Go to the [application page](https://ai.meta.com/resources/models-and-libraries/llama-downloads/) and apply for the access to the model weights.


2. Get the access token from huggingface

Generate a read-only access token on huggingface [here](https://huggingface.co/settings/token), and make sure your huggingface account can access the LLaMA 2 models [here](https://huggingface.co/meta-llama/Llama-2-7b-chat/tree/main).

Fill the access token in the [chatbot-hf.yaml](chatbot-hf.yaml) and [chatbot-meta.yaml](chatbot-meta.yaml) file.
```yaml
envs:
  MODEL_SIZE: 7
  HF_TOKEN: <your-huggingface-token>
```

## Train your own Vicuna on LLaMA 2


