# Train Your Own Vicuna on Llama-2

[Vicuna](https://lmsys.org/blog/2023-03-30-vicuna/) is the first open-source chatbot that performs closely to the ChatGPT, and still is leading the [LLM leaderboard](https://huggingface.co/spaces/lmsys/chatbot-arena-leaderboard) today comparing to all other open-source models.

However, since the LLaMA model it was trained on cannot be used in commercial products, the usage of Vicuna is limited.

[LLaMA 2](https://github.com/facebookresearch/llama/tree/main) comes as a rescue with with a license that authorizes commercial use. We are now closing to see the full potential of the open-source Vicuna.

In this tutorial, we will show you how to train your own Vicuna on LLaMA 2, with your own data, on any cloud, with the help of SkyPilot.

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


### Check Your Training Data

  By default, we use the ShareGPT data and the identity questions in [hardcoded_questions.py](./scripts/hardcoded_questions.py).

  * **Optional**: To use custom data, you can change the data by change the following line in [train.yaml](train.yaml).

  ```yaml
  setup: |
    ...
    wget https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered/resolve/main/ShareGPT_V3_unfiltered_cleaned_split.json  -O $HOME/data/sharegpt.json
    ...
  ```

  The above json file is an array, each element of which has the following format:
  ```json
  {
    "id": "i6IyJda_0",
    "conversations": [
      {
        "from": "human",
        "value": "How to tell if a customer segment is well segmented? In 3 bullet points."
      },
      {
        "from": "gpt",
        "value": "1. Homogeneity: The segment should consist of customers who share similar characteristics and behaviors.\n2. Distinctiveness: The segment should be different from other segments in terms of their characteristics and behaviors.\n3. Stability: The segment should remain relatively stable over time and not change drastically. The characteristics and behaviors of customers within the segment should not change significantly."
      }
    ]
  },
  ```

  * **Optional**: To make the model know about its identity, you can change the hardcoded questions [hardcoded_questiosn.py](./scripts/hardcoded_questions.py)

### Kick Start the Training on Any Cloud

1. Start the training with a single command

  ```bash
  sky launch --down -c vicuna-2 train.yaml \
    --env ARTIFACT_BUCKET_NAME=<your-bucket-name> \
    --env WANDB_API_KEY=<your-wandb-api-key>
  ```

This will launch the training job on the cloud wherever there is available 8x A100-80GB spot GPUs.

> **Tip**: You can get `WANDB_API_KEY` at https://wandb.ai/settings. To disable Weights & Biases, simply leave out that --env flag.

> **Tip**: You can set `ARTIFACT_BUCKET_NAME` to a new bucket name, such as `temp-bucket-1234`, and SkyPilot will create the bucket for you.

2. **Optional**: Try out the training for the 13B model:

  ```bash
  sky launch -c vicuna-2 train.yaml \
    --env ARTIFACT_BUCKET_NAME=<your-bucket-name> \
    --env WANDB_API_KEY=<your-wandb-api-key> \
    --env MODEL_SIZE=13
  ```

### Automatically Recover from Spot Interruptions

[SkyPilot Managed Spot](https://skypilot.readthedocs.io/en/latest/examples/spot-jobs.html) is a library built on top of SkyPilot that helps users to run jobs on spot instances without worrying about the  interruptions. That is the tool used by the LMSYS organization to train the first version of Vicuna (more details can be found in their launching [blog post](https://lmsys.org/blog/2023-03-30-vicuna/) and [example](../vicuna)). With this, the training cost can be reduced from $1000 to **\$300**.

To use SkyPilot Managed Spot, you can simply replace `sky launch` with `sky spot launch` in the above command:

```bash
sky spot launch -n vicuna-2 train.yaml \
  --env ARTIFACT_BUCKET_NAME=<your-bucket-name> \
  --env WANDB_API_KEY=<your-wandb-api-key>
```

