# Fine-tuning Falcon with SkyPilot

This README contains instructions on how to use SkyPilot to finetune [Falcon-7B](https://huggingface.co/tiiuae/falcon-7b) and [Falcon-40B](https://huggingface.co/tiiuae/falcon-40b), an open-source LLM that rivals many current closed-source models, including ChatGPT. 

## Prerequisites
Install SkyPilot from source and follow the [installation guide](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html) to setup your cloud accounts:
```bash
pip install git+https://github.com/skypilot-org/skypilot.git
sky check
```

## Background
Tasks in SkyPilot are defined as YAML files. It contains a `resources` field for specifying the GPUs to use, a `setup` field to install dependencies and `run` field to define the commands to be run as the task. Check out [falcon.yaml](falcon.yaml), which we will be using for fine-tuning Falcon.

GPU memory requirements for fine-tuning Falcon depend on the size of model:
* `tiiuae/falcon-7b` and `tiiuae/falcon-40b` require GPUs with 80GB memory (e.g., `A100-80GB`).
* `ybelkada/falcon-7b-sharded-bf16` requires only 40GB memory (e.g., `A100`).

You can run `sky show-gpus` to list supported GPUs.

## Launching fine-tuning

With the task YAML ready, we can start the fine-tuning of Falcon model on Open Assistant's [Guanaco](https://huggingface.co/datasets/timdettmers/openassistant-guanaco) data **with a single command**.

```bash
sky launch -c falcon -s falcon.yaml --down --env MODEL_NAME=tiiuae/falcon-40b --env WANDB_API_KEY=<wandb key> --env OUTPUT_BUCKET_NAME=<bucket name>
```

In the above command, 
* Set the `OUTPUT_BUCKET_NAME` to some unique name, so the SkyPilot can create a bucket for you to store the model weights. 
* Set `WANDB_API_KEY` to your own key. 
* Set `MODEL_NAME` to the desired base model. Options include `ybelkada/falcon-7b-sharded-bf16`, `tiiuae/falcon-7b`,`tiiuae/falcon-40b`.

When you run this command, SkyPilot will:
1. Find the cheapest available VM across all clouds you have access to.
2. Create and mount your output bucket to the VM to store the model weights.
3. Install all required dependencies and start the fine-tuning.
4. Automatically terminate the instance after the fine-tuning has completed.

For reference, below is a loss graph you may expect to see, and the amount of time and the approximate cost of fine-tuning each of the models (assuming a spot instance GPU rate at $0.39 / hour):

<img width="524" alt="image" src="https://github.com/xzrderek/skypilot/assets/32891260/cdd81781-f5b8-462b-8190-0c1da55f0526">


### Cutting costs with spot instances

To further reduce costs by upto 70%, you can use spot instances. Use `sky spot launch` to launch the same YAML:

```bash
sky spot launch -n falcon train.yaml --env MODEL_NAME=tiiuae/falcon-40b --env WANDB_API_KEY=<wandb key> --env OUTPUT_BUCKET_NAME=<bucket name>
```

SkyPilot [Managed Spot](https://skypilot.readthedocs.io/en/latest/examples/spot-jobs.html) transparently handles any instance failures during fine-tuning. If the spot instance gets preempted, SkyPilot will restart the job on another cloud/region where instances are available. Upon restart, training will resume from the last checkpoint.

### Fine-tuning cost estimates

TODO- add table here (column 'with SkyPilot managed spot')

1. `ybelkada/falcon-7b-sharded-bf16`: 2.5 to 3 hours using 1 A100 GPU; total cost ≈ $1.

2. `tiiuae/falcon-7b`: 2.5 to 3 hours using 1 A100 GPU; total cost ≈ $1.

3. `tiiuae/falcon-40b`: 10 hours using 1 A100-80GB; total cost ≈ $4.


## FAQs

Q: I see some bucket permission errors `sky.exceptions.StorageBucketGetError` when running the above:
```
...
sky.exceptions.StorageBucketGetError: Failed to connect to an existing bucket 'YOUR_OWN_BUCKET_NAME'.
Please check if:
  1. the bucket name is taken and/or
  2. the bucket permissions are not setup correctly. To debug, consider using gsutil ls gs://YOUR_OWN_BUCKET_NAME.
```

A: You need to replace the bucket name with your own globally unique name, and rerun the commands. New private buckets will be automatically created under your cloud account.

Q: Can I fine-tune on other datasets?

A: To finetune using different data, simply add the flag `--env DATASET_NAME=timdettmers/openassistant-guanaco` and replace the path with any huggingface dataset.