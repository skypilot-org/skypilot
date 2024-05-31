# Run GPT-2 in llm.c on any cloud with SkyPilot

This is a reproducible package of llm.c's GPT-2 (124M) training by @karpathy (https://github.com/karpathy/llm.c/discussions/481)
With SkyPilot, you can run GPT-2 (124M) training on any cloud.

## Prerequisites

1. Install [SkyPilot](https://github.com/skypilot-org/skypilot):
```bash
pip install "skypilot-nightly[aws,gcp,azure,kubernetes,lambda,fluidstack]" # Choose the clouds you want to enable
```
2. Enable clouds for SkyPilot:
```bash
sky check
```
Please check the instructions for enabling clouds at [SkyPilot doc](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html).

3. Download the YAML for starting the training:
```bash
wget https://raw.githubusercontent.com/skypilot-org/skypilot/blob/master/llm/gpt-2/gpt2.yaml
```

## Run GPT-2 training

Run the following command to start GPT-2 (124M) training on a GPU VM with 8 A100 GPUs (replace `your-bucket-name` with your bucket name):

```bash
sky launch -c gpt2 gpt2.yaml
```

![GPT-2 training with 8 A100 GPUs](https://imgur.com/v8SGpsF.png)

Or, you can train the model with a single A100, by adding `--gpu A100`:
```bash
sky launch -c gpt2 gpt2.yaml --gpu A100
```

![GPT-2 training with a single A100](https://imgur.com/hN65g4r.png)

## Advanced: Run GPT-2 training in two stages

The data processing for GPT-2 training is CPU-bound, while the training is GPU-bound. Having the data processing on a GPU VM is not cost-effective. With SkyPilot, you can easily
separate the data processing and training into two stages and execute them sequantially manually, or let SkyPilot manage the dependencies between the two stages.

With this data processing can be run on cheaper CPU VMs (e.g., ~\$1.5/hour), and run the training on more expensive GPU VMs (e.g., ~\$1.3-\$3.6/hour for a single A100 GPU, or \$10.3-\$32.8/hour for 8 A100 GPUs).

We can run the data processing on a CPU VM and store the processed data in a cloud bucket. Then, we can run the training on a GPU VM with the processed data.

```bash
wget https://raw.githubusercontent.com//skypilot-org/skypilot/blob/master/llm/gpt-2/gpt2-data.yaml
wget https://raw.githubusercontent.com/skypilot-org/skypilot/blob/master/llm/gpt-2/gpt2-train.yaml
```

### Run two stages manually
#### Data processing

Run the following command to process the training data on a CPU VM and store it in a cloud bucket for future use (replace `your-bucket-name` with your bucket name):

```bash
sky launch -c gpt2-data gpt2-data.yaml --env BUCKET_NAME=your-bucket-name
```


#### Training

After the data is processed, you can then train the model on a GPU VM with 8 A100 GPUs (replace `your-bucket-name` with your bucket name):

```bash
sky launch -c gpt2-train --detach-setup gpt2-train.yaml --env BUCKET_NAME=your-bucket-name
```

Or, you can train the model with a single A100, by adding `--gpu A100`:
```bash
sky launch -c gpt2-train --detach-setup gpt2-train.yaml --gpu A100 --env BUCKET_NAME=your-bucket-name
```


### Run in a Pipeline

We can also combine the two steps into a single SkyPilot job, and let SkyPilot to handle the dependencies between the two steps. Here is an example of how to do this (replace `your-bucket-name` with your bucket name):
```bash
cat gpt2-data.yaml > gpt2-pipeline.yaml
echo "---" >> gpt2-pipeline.yaml
cat gpt2-train.yaml >> gpt2-pipeline.yaml
sky jobs launch -n gpt2 gpt2-pipeline.yaml --env BUCKET_NAME=your-bucket-name
```

SkyPilot will first download and process the dataset on a CPU VM and store the
processed data in a GCS bucket. Then, it will launch a GPT-2 training job on a
GPU VM. The training job will train GPT-2 (124M) on the processed data.

