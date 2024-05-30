# Run GPT-2 in llm.c on any cloud with SkyPilot

This is a reproducible package of llm.c's GPT-2 (124M) training by @karpathy (https://github.com/karpathy/llm.c/discussions/481)
With SkyPilot, you can run GPT-2 (124M) training on any cloud.

## Prerequisites

1. Install [SkyPilot](https://github.com/skypilot-org/skypilot):
```bash
pip install skypilot-nightly
```
2. Enable clouds for SkyPilot:
```bash
sky check
```
Please check the instructions for enabling clouds at [SkyPilot doc](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html).
3. Download the YAMLs in this directory for data processing and training:
```bash
wget https://github.com/skypilot-org/skypilot/blob/master/llm/gpt-2/gpt2-data.yaml
wget https://github.com/skypilot-org/skypilot/blob/master/llm/gpt-2/gpt2-train.yaml
```

## Data processing

Run the following command to process the training data on a CPU VM and store it in a cloud bucket for future use (replace `your-bucket-name` with your bucket name):
```bash
sky launch -c gpt2-data gpt2-data.yaml --env BUCKET_NAME=your-bucket-name
```


## Training

After the data is processed, you can then train the model on a GPU VM with 8 A100 GPUs (replace `your-bucket-name` with your bucket name):

```bash
sky launch -c gpt2-train --detach-setup gpt2-train.yaml --env BUCKET_NAME=your-bucket-name
```

Or, you can train the model with a single A100, by adding `--gpu A100`:
```bash
sky launch -c gpt2-train --detach-setup gpt2-train.yaml --gpu A100 --env BUCKET_NAME=your-bucket-name
```


## Run in a Pipeline

We can also combine the two steps into a single SkyPilot job, and let SkyPilot to handle the dependencies between the two steps. Here is an example of how to do this (replace `your-bucket-name` with your bucket name):
```bash
cat gpt2-data.yaml > gpt2.yaml
echo "---" >> gpt2.yaml
cat gpt2-train.yaml >> gpt2.yaml
sky jobs launch -n gpt2 gpt2.yaml --env BUCKET_NAME=your-bucket-name
```

SkyPilot will first download and process the dataset on a CPU VM and store the
processed data in a GCS bucket. Then, it will launch a GPT-2 training job on a
GPU VM. The training job will train GPT-2 (124M) on the processed data.

