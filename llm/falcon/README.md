# Finetuning Falcon with SkyPilot

This README contains instructions on how to use SkyPilot to finetune Falcon-7B and Falcon-40B, an open-source LLM that rivals many current closed-source models, including ChatGPT. 

* [Blog post](https://huggingface.co/blog/falcon)
* [Repo](https://huggingface.co/tiiuae/falcon-40b)
* [Training code](https://gist.github.com/pacman100/1731b41f7a90a87b457e8c5415ff1c14)


## Prerequisites
Install the latest SkyPilot and check your setup of the cloud credentials:
```bash
pip install git+https://github.com/skypilot-org/skypilot.git
sky check
```
See the Falcon SkyPilot YAML for [training](train.yaml). Serving is currently a work in progress and a YAML will be provided for that soon! We are also working on adding an evaluation step to evaluate the model you finetuned compared to the base model.

## Running Falcon on SkyPilot
Finetuning `Falcon-7B` and `Falcon-40B` require GPUs with 80GB memory, 
but `Falcon-7b-sharded` requires only 40GB memory. Thus,
* If your GPU has 40 GB memory or less (e.g., Nvidia A100): use `ybelkada/falcon-7b-sharded-bf16`.
* If your GPU has 80 GB memory (e.g., Nvidia A100-80GB): you can also use `tiiuae/falcon-7b` and `tiiuae/falcon-40b`. 

Try `sky show-gpus --all` for supported GPUs.

We can start the finetuning of Falcon model on Open Assistant's [Guanaco](https://huggingface.co/datasets/timdettmers/openassistant-guanaco) data **with a single command**. It will automatically find the available cheapest VM on any cloud.

**To finetune using different data**, simply replace the path in `timdettmers/openassistant-guanaco` with any other huggingface dataset.

Steps for training on your cloud(s):

1. In [train.yaml](train.yaml), set the following variables in `envs`:

    - Replace the `OUTPUT_BUCKET_NAME` with a unique name. SkyPilot will create this bucket for you to store the model weights.
    - Replace the `WANDB_API_KEY` to your own key. 
    - Replace the `MODEL_NAME` with your desired base model. 

2.  **Training the Falcon model using spot instances**:

```bash
sky spot launch -n falcon falcon.yaml
```

Currently, such `A100-80GB:1` spot instances are only available on AWS and GCP.

[Optional] **To use on-demand `A100-80GB:1` instances**, which are currently available on Lambda Cloud, Azure, and GCP:
```bash
sky launch -c falcon -s falcon.yaml --no-use-spot
```

For reference, below is a loss graph you may expect to see, and the amount of time and the approximate cost of fine-tuning each of the models over 500 epochs (assuming a spot instance A100 GPU rate at $1.1 / hour and a A100-80GB rate of $1.61 / hour):

<img width="524" alt="image" src="https://imgur.com/BDlHink.png">

1. `ybelkada/falcon-7b-sharded-bf16`: 2.5 to 3 hours using 1 A100 spot GPU; total cost ≈ $3.3.

2. `tiiuae/falcon-7b`: 2.5 to 3 hours using 1 A100 spot GPU; total cost ≈ $3.3.

3. `tiiuae/falcon-40b`: 10 hours using 1 A100-80GB spot GPU; total cost ≈ $16.10.


## Q&A

Q: I see some bucket permission errors `sky.exceptions.StorageBucketGetError` when running the above:
```
...
sky.exceptions.StorageBucketGetError: Failed to connect to an existing bucket 'YOUR_OWN_BUCKET_NAME'.
Please check if:
  1. the bucket name is taken and/or
  2. the bucket permissions are not setup correctly. To debug, consider using gsutil ls gs://YOUR_OWN_BUCKET_NAME.
```

A: You need to replace the bucket name with your own globally unique name, and rerun the commands. New private buckets will be automatically created under your cloud account.
