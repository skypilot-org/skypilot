# GPT-2 (124M) in llm.c in 90 minutes

https://github.com/karpathy/llm.c/discussions/481

```bash
sky jobs launch -n gpt2 gpt2.yaml
```

SkyPilot will first download and process the dataset on a CPU VM and store the
processed data in a GCS bucket. Then, it will launch a GPT-2 training job on a
GPU VM. The training job will train GPT-2 (124M) on the processed data.

