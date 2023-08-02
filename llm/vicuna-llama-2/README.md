# Train Your Own Vicuna on Llama-2

[Vicuna](https://lmsys.org/blog/2023-03-30-vicuna/) is the first open-source chatbot that performs closely to the ChatGPT, and still is leading the [LLM leaderboard](https://huggingface.co/spaces/lmsys/chatbot-arena-leaderboard) today comparing to all other open-source models.

With the latest [Llama-2](https://github.com/facebookresearch/llama/tree/main), we are now able to get a Vicuna model that can be used commercially.

In this tutorial, we will show you how to train your own Vicuna on Llama-2, with your own data, on any cloud, with the help of SkyPilot.

## Pre-requisites

1. Apply for access to the Llama-2 model

Go to the [application page](https://ai.meta.com/resources/models-and-libraries/llama-downloads/) and apply for access to the model weights.


2. Get an access token from HuggingFace

Generate a read-only access token on HuggingFace [here](https://huggingface.co/settings/token). Go to the HuggingFace page for Llama-2 models [here](https://huggingface.co/meta-llama/Llama-2-7b-chat/tree/main) and apply for access. Ensure your HuggingFace email is the same as the email on the Meta request. It may take 1-2 days for approval.

Put the access token into [train.yaml](train.yaml):
```yaml
envs:
  HF_TOKEN: <your-huggingface-token>  # Change to your own huggingface token
```

## Train your own Vicuna on Llama-2


### Check training data

  By default, we use the ShareGPT data and the identity questions in [hardcoded_questions.py](./scripts/hardcoded_questions.py).

  * **Optional**: To use custom data, you can change the following line in [train.yaml](train.yaml):

  ```yaml
  setup: |
    ...
    wget https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered/resolve/main/ShareGPT_V3_unfiltered_cleaned_split.json  -O $HOME/data/sharegpt.json
    ...
  ```

  The above json file is an array, each element of which having the following format:
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

  * **Optional**: To make the model know about its identity, you can change the hardcoded questions [hardcoded_questions.py](./scripts/hardcoded_questions.py)

### Kick start training on any cloud

* Start training with a single command

  ```bash
  sky launch --down -c vicuna train.yaml \
    --env ARTIFACT_BUCKET_NAME=<your-bucket-name> \
    --env WANDB_API_KEY=<your-wandb-api-key>
  ```

This will launch the training job on the cheapest cloud that has available 8x A100-80GB spot GPUs.

> **Tip**: You can get `WANDB_API_KEY` at https://wandb.ai/settings. To disable Weights & Biases, simply leave out that --env flag.

> **Tip**: You can set `ARTIFACT_BUCKET_NAME` to a new bucket name, such as `temp-bucket-1234`, and SkyPilot will create the bucket for you.

*Use on-demand instead to unlock more clouds*: Inside ``train.yaml`` we requested using spot instances:
```yaml
resources:
  accelerators: A100-80GB:8
  disk_size: 1000
  use_spot: true
```
However, spot A100-80GB:8 is currently only supported on GCP. On-demand versions are supported on AWS, Azure, GCP, and Lambda. (Hint: check out the handy outputs of `sky show-gpus A100-80GB:8`!)

To use those clouds, add the `--no-use-spot` flag to request on-demand instances:
```console
sky launch --no-use-spot ...
```

* **Optional**: Try out the training for the 13B model:

  ```bash
  sky launch -c vicuna train.yaml \
    --env ARTIFACT_BUCKET_NAME=<your-bucket-name> \
    --env WANDB_API_KEY=<your-wandb-api-key> \
    --env MODEL_SIZE=13
  ```

### Automatically recover from spot interruptions

[SkyPilot Managed Spot](https://skypilot.readthedocs.io/en/latest/examples/spot-jobs.html) is a library built on top of SkyPilot that helps users run jobs on spot instances without worrying about interruptions. That is the tool used by the LMSYS organization to train the first version of Vicuna (more details can be found in their [launch blog post](https://lmsys.org/blog/2023-03-30-vicuna/) and [example](../vicuna)). With this, the training cost can be reduced from $1000 to **\$300**.

To use SkyPilot Managed Spot, you can simply replace `sky launch` with `sky spot launch` in the above command:

```bash
sky spot launch -n vicuna train.yaml \
  --env ARTIFACT_BUCKET_NAME=<your-bucket-name> \
  --env WANDB_API_KEY=<your-wandb-api-key>
```

### Serve your model

After the training is done, you can serve your model with the following command:

```bash
sky launch -c serve serve.yaml --env MODEL_CKPT=<your-bucket-name>/chatbot/7b
```

> **Tip**: You can also switch to a cheaper accelerator, such as L4, to save cost, by adding `--gpus L4` to the above command.
