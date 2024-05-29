# GPT-2 (124M) in llm.c in 90 minutes

https://github.com/karpathy/llm.c/discussions/481

## Data processing

```bash
sky launch -c gpt2-data gpt2-data.yaml -y
```


## Training

```bash
sky launch -c gpt2-train gpt2-train.yaml -y
```


## Run in a Pipeline
We can also combine the two steps into a single SkyPilot job:
```bash
cat gpt2-data.yaml > gpt2.yaml
echo "---" >> gpt2.yaml
cat gpt2-train.yaml >> gpt2.yaml
sky jobs launch -n gpt2 gpt2.yaml
```

SkyPilot will first download and process the dataset on a CPU VM and store the
processed data in a GCS bucket. Then, it will launch a GPT-2 training job on a
GPU VM. The training job will train GPT-2 (124M) on the processed data.

