# Run GPT-2 (124M) in llm.c on any cloud with SkyPilot

This is a reproducible package of llm.c's GPT-2 (124M) training by @karpathy (https://github.com/karpathy/llm.c/discussions/481)
With SkyPilot, you can run GPT-2 (124M) training on any cloud.

## Data processing

```bash
sky launch -c gpt2-data gpt2-data.yaml --env BUCKET_NAME=your-bucket-name
```


## Training

```bash
sky launch -c gpt2-train --detach-setup gpt2-train.yaml --env BUCKET_NAME=your-bucket-name
```


## Run in a Pipeline
We can also combine the two steps into a single SkyPilot job:
```bash
cat gpt2-data.yaml > gpt2.yaml
echo "---" >> gpt2.yaml
cat gpt2-train.yaml >> gpt2.yaml
sky jobs launch -n gpt2 gpt2.yaml --env BUCKET_NAME=your-bucket-name
```

SkyPilot will first download and process the dataset on a CPU VM and store the
processed data in a GCS bucket. Then, it will launch a GPT-2 training job on a
GPU VM. The training job will train GPT-2 (124M) on the processed data.

