# Vicuna: An LLM Chatbot Impressing GPT-4 with 90% ChatGPT Quality

<img src="https://vicuna.lmsys.org/favicon.jpeg" width="25%" alt="Vicuna LLM"/>

Vicuna is an LLM chatbot with impressive quality. It is trained using SkyPilot on [cloud spot instances](https://skypilot.readthedocs.io/en/latest/examples/spot-jobs.html), with a cost of ~$300.

* [Blog post](https://vicuna.lmsys.org/)
* [Demo](https://chat.lmsys.org/)
* [Repo](https://github.com/lm-sys/FastChat)

## Prerequisites
Install the latest SkyPilot and check your setup of the cloud credentials:
```bash
pip install git+https://github.com/skypilot-org/skypilot.git
sky check
```
Find the Vicuna finetuning [SkyPilot YAMLs](examples/llm-vicuna/finetune.yaml)
Find the Vicuna serving [SkyPilot YAMLs](examples/llm-vicuna/serve.yaml)

## Serve the official Vicuna model by yourself with SkyPilot

1. **Serve the official Vicuna model**:
```bash
sky launch -c vicuna-serve -s serve.yaml
```
2. Serve the 13B model instead of the default 7B:
```bash
sky launch -c vicuna-serve -s serve.yaml --env MODEL_SIZE=13
```


## Finetuning Vicuna with SkyPilot
Currently, training requires GPUs with 80GB memory.  See `sky show-gpus --all` for supported GPUs.

We can start the finetuning of Vicuna model on the dummy data [dummy.json](examples/llm-vicuna/dummy-data) **with a single command**. It will automatically find the available cheapest VM on any cloud.

To finetune on your own data, replace the file with your own, or change the line `/data/mydata.json: ./dummy.json` to the path of your own data.

1. Replace the bucket name in [finetune.yaml](examples/llm-vicuna/finetune.yaml) with some unique name, so the SkyPilot can create a bucket for you to store the model weights. See `# Change to your own bucket` in the YAML file.

2. **Finetune the 7B model on 8 A100 GPUs (80GB memory) using spot instances**:
```bash
# Launch it on managed spot to save 3x cost
sky spot launch -n vicuna scripts/train-vicuna.yaml --env WANDB_API_KEY
```

[Optional] Finetune a larger model 13B model
```
# Train a 13B model instead of the default 7B
sky spot launch -n vicuna-7b scripts/train-vicuna.yaml --env WANDB_API_KEY --env MODEL_SIZE=13

# Use *unmanaged* spot instances (i.e., preemptions won't get auto-recovered).
# Unmanaged spot saves the cost of a small controller VM.  We recommend using managed spot as above.
sky launch -n vicuna scripts/train-vicuna.yaml --env WANDB_API_KEY
```
Currently, such `A100-80GB:8` spot instances are only available on AWS and GCP.

[Optional] **To use on-demand `A100-80GB:8` instances**, which are currently available on Lambda Cloud, Azure, and GCP:
```bash
sky launch -c vicuna -s scripts/train-vicuna.yaml --env WANDB_API_KEY --no-use-spot
```



## Q&A

Q: I see some bucket permission errors `sky.exceptions.StorageBucketGetError` when running the above:
```
...
sky.exceptions.StorageBucketGetError: Failed to connect to an existing bucket 'model-weights'.
Please check if:
  1. the bucket name is taken and/or
  2. the bucket permissions are not setup correctly. To debug, consider using gsutil ls gs://model-weights.
```

A: The YAML files hard-coded names for existing buckets which are not public. Replace those bucket names (see `# Change to your own bucket`) with some unique names, and rerun the commands. New private buckets will be automatically created under your cloud account.
