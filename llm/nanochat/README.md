# Run nanochat on any cloud or Kubernetes with SkyPilot
![Run nanochat on Your Infra](https://i.imgur.com/2MVlTsh.png)

This demo shows how to train and serve [nanochat](https://github.com/karpathy/nanochat) on any cloud provider or Kubernetes cluster with [SkyPilot](https://docs.skypilot.co/en/latest/docs/index.html). Run nanochat seamlessly across AWS, GCP, Azure, Lambda Labs, Nebius and more - or bring your own Kubernetes infrastructure.

## What is nanochat?

> The best ChatGPT that $100 can buy.

[nanochat](https://github.com/karpathy/nanochat) by [Andrej Karpathy](https://github.com/karpathy) is a full-stack LLM training pipeline that runs the complete flow (tokenization -> pretraining -> finetuning -> evaluation -> inference -> web UI) on a single 8×H100 node in ~4 hours for ~$100.

## Training: Running the Speedrun Pipeline

Once SkyPilot is set up (see [Appendix: Preparation](#appendix-preparation)), launch the speedrun training pipeline on any cloud provider:

```bash
sky launch -c nanochat-speedrun speedrun.sky.yaml --infra <k8s|aws|gcp|nebius|lambda|etc>
```

This will:
- Provision an 8×H100 GPU node
- Set up the environment
- Run the complete training pipeline via `speedrun.sh`
- Save trained model checkpoints to `s3://nanochat-data` (change this to your own bucket)
- Complete in approximately 4 hours (~$100 on most providers)

### Monitoring Training Progress

```bash
# View logs
sky logs nanochat-speedrun

# SSH to check the report card with evaluation metrics
ssh nanochat-speedrun
cat report.md
```

## Serving: Deploy Your Trained Model

Once training is complete, serve your trained model with the web UI:

```bash
sky launch -c nanochat-serve serve.sky.yaml --infra <k8s|aws|gcp|nebius|lambda|etc>
```

This will:
- Provision a 1×H100 GPU node (much cheaper than the 8×H100 VM used for training)
- Load model weights from the same `s3://nanochat-data` bucket used during training
- Serve the web chat interface on port 8000
- Cost is ~$2-3/hour on most providers

### Accessing the Web UI

Get the endpoint URL to access the chat interface:

```bash
sky status --endpoint 8000 nanochat-serve
```

Open the displayed URL in your browser to chat with your trained model!

![nanochat web UI](https://github.com/user-attachments/assets/ee8b1536-1faa-435a-ab22-4db2c8cf9220)

## Customizing Your Training

SkyPilot YAMLs are flexible and can be customized to fit your use case. 

### Custom Storage Bucket

To use your own bucket for storing the model weights, replace `s3://nanochat-data` in the YAML:

```yaml
file_mounts:
  /tmp/nanochat:
    source: s3://your-bucket-name  # or gs://your-bucket, r2://, cos://<region>/<bucket>, oci://<bucket_name>
```

## Appendix: Preparation

### 1. Install SkyPilot

```bash
pip install skypilot-nightly[aws,gcp,nebius,lambda,kubernetes]
# or other clouds (17+ clouds and kubernetes are supported)
# See: https://docs.skypilot.co/en/latest/getting-started/installation.html
```

### 2. Check your infra setup

```bash
sky check

🎉 Enabled clouds 🎉
    ✔ AWS
    ✔ GCP
    ...
    ✔ Kubernetes
```

### 3. Configure storage access

Make sure your cloud credentials have read/write access to the bucket specified in the YAML files. See SkyPilot's [Cloud Buckets docs](https://docs.skypilot.co/en/latest/reference/storage.html) page for more details. 

## Learn More

* [nanochat GitHub](https://github.com/karpathy/nanochat) - Project repository and full documentation
* [Announcement tweet](https://x.com/karpathy/status/1977755427569111362) - Andrej Karpathy's announcement
* [SkyPilot Docs](https://docs.skypilot.co) - SkyPilot documentation
